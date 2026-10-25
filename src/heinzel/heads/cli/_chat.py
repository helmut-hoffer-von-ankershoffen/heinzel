"""`hz chat`: the chat in a terminal, streaming the answer and asking y/N before anything changes (ADR 0045).

A view on the chat operations of `system.runner.chat` (`open`, `send`, `confirm`, `decline`, `show`, `list`), like the
web app's right sidebar: each message is sent with `send`, the answer is printed as it streams in, and every action
the agent proposed is shown as its command line with a `Run it? [y/N]` question. Only a `y` runs it, through
`confirm`; anything else declines it. `hz chat --continue` picks up the most recent session.
"""

from collections.abc import Callable, Mapping
from typing import Annotated, Any, cast

import typer
from pydantic_core import to_jsonable_python
from rich.console import Console
from rich.markup import escape
from rich.text import Text

from heinzel.heads._presence import mark_presence
from heinzel.heads.cli._progress import ConsoleProgress
from heinzel.heads.cli._render import TerminalSafe, message, terminal_safe
from heinzel.kernel import CurrentUser, HeinzelError, Kernel, OperationLet

CHAT = "system.runner.chat"
"""The module whose operations `hz chat` is a view on."""


def command_words(path: tuple[str, ...]) -> tuple[str, ...]:
    """The words of an operation's CLI command, `hz chat ...` for the chat's (mounted at the top level only)."""
    prefix = tuple(CHAT.split("."))
    return ("chat", *path[len(prefix) :]) if path[: len(prefix)] == prefix else path


HELP = (
    "Chat with the agent runner in this terminal: it reads with hz's operations and asks you before it changes "
    "anything. /exit or Ctrl-D leaves; `hz chat --continue` comes back."
)
EXIT = frozenset({"/exit", "/quit", "/q"})
YES = frozenset({"y", "yes"})
SHOWN = 6
"""Messages of a resumed session shown again before the prompt."""

Read = Callable[[str], str]
Session = dict[str, Any]


class ChatRepl:
    """The interactive loop behind `hz chat`."""

    def __init__(
        self,
        kernel: Kernel,
        console: Console,
        err_console: Console,
        progress: ConsoleProgress,
        read: Read | None = None,
    ) -> None:
        """Talk on `console`, report on `err_console`; `read` replaces the terminal's input in tests."""
        self.kernel = kernel
        self.console = console
        self.err_console = err_console
        self.progress = progress
        self.read: Read = read or console.input
        self._streamed = False

    def _invoke(self, name: str, **kwargs: object) -> Session:
        operation = next(
            (let for record, let in self.kernel.lets(OperationLet) if record.qualname == CHAT and let.name == name),
            None,
        )
        if operation is None:
            msg = f"The chat is not available: no module provides {CHAT} {name}."
            raise HeinzelError(msg)
        # The chat's operations return pydantic models (a session) or lists of them; as JSON, they are plain dicts.
        with self.kernel.resolve(CurrentUser).local("cli"):
            mark_presence(self.kernel, "system.runner.chat")
            return cast("Session", to_jsonable_python(self.kernel.invoke(operation, **kwargs)))

    def start(self, *, session: str | None, resume: bool, context: str | None, detail: str | None) -> Session:
        """The session to talk in: the one named, the most recent one, or a new one.

        Raises:
            HeinzelError: If the session is unknown, or there is none to continue.
        """
        if session is not None:
            return self._invoke("show", session=session)
        if resume:
            found = cast("list[Session]", self._invoke("list"))  # `list` returns summaries, not one session
            if not found:
                msg = "There is no chat to continue; `hz chat` opens one."
                raise HeinzelError(msg)
            return self._invoke("show", session=found[0]["id"])
        return self._invoke("open", context=context, detail=detail)

    def run(self, chat: Session) -> None:
        """Talk until the person leaves; every proposed action is asked about before the next message."""
        self._header(chat)
        for item in chat["messages"][-SHOWN:]:
            self._show(item)
        chat, leaving = self._decide(chat)
        while not leaving:
            try:
                line = self.read("[bold]you >[/bold] ").strip()
            except (EOFError, KeyboardInterrupt):
                self.console.print()
                break
            if line in EXIT:
                break
            if not line:
                continue
            chat = self._send(chat, line)
            chat, leaving = self._decide(chat)
        self.err_console.print(f"Chat {chat['id']} kept; `hz chat --continue` comes back to it.", highlight=False)

    def _header(self, chat: Session) -> None:
        context = chat.get("context")
        about = f" about {context['kind']} {context['ref']}".rstrip() if context else ""
        self.err_console.print(
            TerminalSafe(Text(f"Chat {chat['id']}{about}. {_costs(chat)} Type /exit to leave.", style="dim")),
            soft_wrap=True,
        )

    def _send(self, chat: Session, line: str) -> Session:
        self._streamed = False
        self.progress.listener = self._event
        try:
            answered = self._invoke("send", session=chat["id"], message=line)
        except HeinzelError as exc:
            self.err_console.print(message("Error:", str(exc)), soft_wrap=True)
            return chat
        except KeyboardInterrupt:  # the agent is stopped with its process group; what it spent still counts
            self.console.print()
            self.err_console.print(message("Stopped:", "you interrupted the answer"), soft_wrap=True)
            return self._invoke("show", session=chat["id"])
        finally:
            self.progress.listener = None
        reply = answered["messages"][-1]
        if self._streamed:
            self.console.print()
        if not self._streamed or reply["status"] != "completed":
            self._show(reply)
        cost, cap = reply.get("cost_usd"), reply.get("budget_usd")
        spent = f"${cost:.4f}" if cost is not None else "cost not reported"
        if cost is not None and reply.get("billing") == "plan":  # API prices, but the subscription pays (ADR 0057)
            spent = f"uses your plan · ≈ ${cost:.2f} at API prices"
        if cost is None and cap:
            spent += f", counted as ${cap:.2f}"
        self.err_console.print(
            TerminalSafe(Text(f"{reply.get('runner') or 'agent'} · {spent} · {_costs(answered)}", style="dim")),
            soft_wrap=True,
        )
        return answered

    def _event(self, kind: str, data: Mapping[str, object]) -> None:
        if kind == "text" and isinstance(text := data.get("text"), str):
            self._streamed = True
            self.console.print(terminal_safe(text), end="", markup=False, highlight=False, soft_wrap=True)
        elif kind == "tool" and isinstance(name := data.get("name"), str):
            self.err_console.print(f"[dim]· {escape(terminal_safe(name))}[/dim]")

    def _show(self, item: Session) -> None:
        role = item["role"]
        if role == "user":
            self.console.print(f"[bold]you >[/bold] {escape(terminal_safe(item['text']))}", soft_wrap=True)
        elif role == "action":
            self.err_console.print(TerminalSafe(Text(item["text"], style="dim")), soft_wrap=True)
        elif item["status"] != "completed":
            stopped = item.get("detail") or item["status"]
            self.err_console.print(message("Stopped:", stopped), soft_wrap=True)
            if item.get("fix"):
                self.err_console.print(TerminalSafe(Text.assemble(("Fix: ", "bold"), item["fix"])), soft_wrap=True)
        else:
            self.console.print(TerminalSafe(Text(item["text"])), soft_wrap=True)

    def _decide(self, chat: Session) -> tuple[Session, bool]:
        """Ask about each pending action; True when the person left (Ctrl-D, Ctrl-C) and the rest stay pending."""
        for action in [a for a in chat["actions"] if a["status"] == "pending"]:
            self.console.print(
                TerminalSafe(Text.assemble(("The agent proposes: ", "yellow"), action["command"])), soft_wrap=True
            )
            try:
                answer = self.read("Run it? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                self.console.print()
                return chat, True  # leaving is no answer: it stays pending for `hz chat --continue`
            name = "confirm" if answer in YES else "decline"
            try:
                chat = self._invoke(name, session=chat["id"], action=action["id"])
            except HeinzelError as exc:
                self.err_console.print(message("Error:", str(exc)), soft_wrap=True)
                continue
            self._show(chat["messages"][-1])
        return chat, False


def _costs(chat: Session) -> str:
    costs = chat["costs"]
    return (
        f"Each message may cost up to ${costs['message_max_usd']:.2f}; this chat has cost ${costs['spent_usd']:.2f} "
        f"of ${costs['session_max_usd']:.2f}."
    )


def chat_callback(
    kernel: Kernel, console: Console, err_console: Console, progress: ConsoleProgress
) -> Callable[..., None]:
    """The callback of the `chat` group: without a subcommand, `hz chat` is the interactive chat."""

    def chat(
        ctx: typer.Context,
        resume: Annotated[
            bool, typer.Option("--continue", "-c", help="Continue the most recent chat instead of opening one.")
        ] = False,
        session: Annotated[str | None, typer.Option(help="Continue this chat session (`hz chat list`).")] = None,
        context: Annotated[
            str | None,
            typer.Option(
                help="What a new chat is about: `route:/contracts`, `contract:weekly`, `draft:<id>`, `event:<id>`."
            ),
        ] = None,
        detail: Annotated[str | None, typer.Option(help="What you know about it, in words.")] = None,
    ) -> None:
        if ctx.invoked_subcommand is not None:
            return
        repl = ChatRepl(kernel, console, err_console, progress)
        try:
            started = repl.start(session=session, resume=resume, context=context, detail=detail)
        except HeinzelError as exc:
            err_console.print(message("Error:", str(exc)), soft_wrap=True)
            raise typer.Exit(1) from exc
        repl.run(started)

    chat.__doc__ = HELP
    return chat
