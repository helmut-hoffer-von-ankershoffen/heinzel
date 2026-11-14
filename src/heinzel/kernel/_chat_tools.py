"""One tool policy for a Heinzel's chat turn: the same rule on every path a turn calls tools by (ADR 0045, 0162).

A Heinzel calls hz's operations in two ways. A runner that drives its model inside hz (the engine runner, ADR 0159)
calls them in-process, as `ChatTool`s; a runner that runs an agent CLI (Claude Code) can take tools only over MCP,
so hz starts a private stdio pipe for that one turn, `hz system serve mcp --chat <session>`, and the MCP head serves
the very same `ChatTool`s there. Both paths select the tools with `chat_tools` and run every call through
`ChatTool.call`, so what runs, what is proposed and what is refused cannot differ between them:

- a read-only operation runs, and its result comes back as JSON without email addresses, cut at `RESULT_LIMIT`;
- any other operation does not run: it is handed to the `ActionGate` as a proposal, and the call returns its id;
- the one consumer of a person's prior authorization (`system runner chat confirm-authorized-mcp`) runs only for
  its own chat session; its proof never appears in arguments or results;
- a tool of the built-in browser runs for the chat's own session, also when it is not read-only, unless it is a
  commit (send, buy, delete): that one is proposed like any other change, pinned to the target that was described.
  This is the one seam in the rule above, and it is open to the browser module only;
- a failure reaches the agent as a `ChatToolError` without email addresses; a bug's message never does.

The kernel knows neither chats nor MCP: the chat module provides `ActionGate` and `McpChatSessions`, the MCP head
and the runners ask for them by type.
"""

import base64
import inspect
import json
import logging
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, create_model
from pydantic_core import to_jsonable_python

from heinzel.kernel._agents import ChatToolSet, ToolProfile
from heinzel.kernel._browser import (
    BROWSER_MODULE,
    BrowserBridge,
    BrowserCaller,
    BrowserCommitError,
    PageImage,
    browser_caller,
)
from heinzel.kernel._browser_relay import relay
from heinzel.kernel._chat import ActionGate, ProposedCall
from heinzel.kernel._chat_services import McpChatSessions
from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._exposure import Exposed, exposed, input_schema
from heinzel.kernel._identity import CurrentUser
from heinzel.kernel._kernel import Kernel
from heinzel.kernel._logging import log_event
from heinzel.kernel._presence_marks import mark_authenticated_presence
from heinzel.kernel._text import redact_emails

if TYPE_CHECKING:
    from pydantic_ai import Tool

log = logging.getLogger(__name__)

RESULT_LIMIT = 20000
"""Characters of a tool result an agent in a chat gets; the rest is cut, so one call can't fill its context."""
SERVER = "hz"
"""hz's name among a turn's tool sources: its tools are `mcp__hz__<tool>` on both paths, as the chat's rules say."""
HEAD = "mcp"
"""The exposure a chat's tools follow: the MCP head's own, so what a UI hides from beginners is still a tool."""
CONFIRM_AUTHORIZED = ("system.runner.chat", "confirm-authorized-mcp")
AUTHENTICATION: Final = "mcp-stdio"
"""The identity a chat's reads run with, in the stdio child and in-process alike: the trusted local installation."""

Kind = Literal["operation", "confirm-authorized"]


class ChatToolError(HeinzelError):
    """A chat tool call that failed or was refused; its message is safe to show the agent (no email addresses)."""


def command_line(item: Exposed, arguments: Mapping[str, Any]) -> str:
    """The call as the person would type it: `hz system config set --key a.b --value 1`."""
    words = ["hz", *item.path]
    for name, value in arguments.items():
        if value is None:
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        words += [f"--{name.replace('_', '-')}", shlex.quote(text)]
    return " ".join(words)


def agent_item(item: Exposed) -> Exposed:
    """The operation as an agent may call it: never with a bearer approval proof (schedule control's)."""
    if item.record.qualname != "heinzel" or item.operation.name != "schedule-control":
        return item
    parameters = tuple(parameter for parameter in item.parameters if parameter.name != "approval_proof")
    return replace(item, parameters=parameters)


@dataclass(frozen=True, slots=True)
class ChatTool:
    """One of hz's operations as a tool of one chat session's agent; `call` is the one policy (ADR 0162)."""

    kernel: Kernel
    item: Exposed
    session: str
    kind: Kind = "operation"

    def __post_init__(self) -> None:
        """Hold the operation as an agent may call it, however the tool was built (never with an approval proof)."""
        if self.kind == "operation":
            object.__setattr__(self, "item", agent_item(self.item))

    @property
    def name(self) -> str:
        """The tool's flat name, `system_doctor` for `hz system doctor`."""
        return self.item.name

    @property
    def description(self) -> str:
        """The operation's one-line summary."""
        return self.item.operation.summary

    @property
    def read_only(self) -> bool:
        """Whether a call runs at once; any other call is proposed, or is the scoped authorized confirmation."""
        return self.kind == "operation" and self.item.operation.read_only

    def schema(self) -> dict[str, Any]:
        """The JSON Schema of the tool's arguments."""
        return input_schema(self.item)

    def call(self, arguments: Mapping[str, Any]) -> object:
        """Run, propose or refuse one call by the chat's rule; the result is what the agent gets.

        A read and a proposal return text; the authorized confirmation returns its operation's own result, and a
        picture of a browser tab is a `PageImage`.

        Raises:
            ChatToolError: If the call is refused or fails; the message holds no email address.
        """
        if self.kind == "operation" and self.item.operation.private and self.item.operation.read_only:
            # The durable engine grants private data; a chat's local read identity must never reach it.
            msg = f"{self.name} reads private data, which no chat tool may"
            raise ChatToolError(msg)
        user = self.kernel.resolve(CurrentUser)
        try:
            arguments = self.arguments(arguments)
            # Whatever the caller's thread carries (a signed-in web request) is never the agent's identity.
            with user.using(None):
                if self.kind == "confirm-authorized":
                    return self._confirm(user, arguments)
                if self.item.record.qualname == BROWSER_MODULE:
                    return self._browser(user, arguments)
                if self.item.operation.read_only:
                    return self._read(user, arguments)
                return self._propose(arguments)
        except ChatToolError:
            raise
        except HeinzelError as exc:
            raise ChatToolError(redact_emails(str(exc))) from exc
        except Exception as exc:  # a bug's message may hold anything: the agent gets its type only
            log_event(log, "mcp.chat_tool.failed", level=logging.ERROR, exc_info=True, tool=self.name)
            msg = f"{self.name} failed with a bug ({type(exc).__name__}); the person can see the log."
            raise ChatToolError(msg) from exc

    def arguments(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """The call's arguments checked against the tool's own parameters, with their defaults, as MCP passes them.

        A name the tool does not declare is dropped, as the MCP SDK drops it: the agent can never pass what the
        kernel injects (a service, the caller's identity) or what no agent may give (an approval proof).

        Raises:
            ChatToolError: If an argument is missing or of the wrong kind.
        """
        fields: dict[str, Any] = {
            parameter.name: (
                Any if parameter.annotation is inspect.Parameter.empty else parameter.annotation,
                ... if parameter.default is inspect.Parameter.empty else parameter.default,
            )
            for parameter in self.item.parameters
        }
        model = create_model(
            f"{self.name}_arguments", __config__=ConfigDict(extra="ignore", arbitrary_types_allowed=True), **fields
        )
        try:
            checked = model.model_validate(_pre_parsed(dict(arguments), model))
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors())
            msg = redact_emails(f"Invalid arguments for {self.name}: {problems}")
            raise ChatToolError(msg) from None
        return {name: getattr(checked, name) for name in fields}

    def _browser(self, user: CurrentUser, arguments: Mapping[str, Any]) -> object:
        """A browser tool: it runs for this chat's session unless the browser's gate says it is a commit.

        The gate is the browser module's, not this policy's: a commit leaves the operation as `BrowserCommitError`
        without having run, and is proposed with the fingerprint of the target the person will be shown.
        """
        if not self.kernel.resolve(BrowserBridge).available:
            # This process has no desktop (a turn's private pipe, a terminal): the one that has it runs the call,
            # as this chat's session and by the tool's name only, so gate, proposal, limits and log are there.
            jsonable = to_jsonable_python(dict(arguments), fallback=str)
            answer = relay(self.kernel.settings.state_dir, "heinzel", self.session, self.name, jsonable)
            if answer is not None:
                return _relayed(answer)
        try:
            with browser_caller(BrowserCaller(actor=self.session, kind="heinzel")), user.local_read(AUTHENTICATION):
                result = self.kernel.invoke(self.item.operation, **arguments)
        except BrowserCommitError as commit:
            return self._propose({**arguments, "expected": commit.expected}, command=commit.summary)
        return result if isinstance(result, PageImage) else _result_text(result)

    def _read(self, user: CurrentUser, arguments: Mapping[str, Any]) -> str:
        with user.local_read(AUTHENTICATION):
            result = self.kernel.invoke(self.item.operation, **arguments)
        return _result_text(result)

    def _propose(self, arguments: Mapping[str, Any], *, command: str | None = None) -> str:
        jsonable = to_jsonable_python(dict(arguments), fallback=str)
        call = ProposedCall(
            session=self.session,
            module=self.item.record.qualname,
            operation=self.item.operation.name,
            tool=self.name,
            arguments=jsonable,
            command=command or command_line(self.item, jsonable),
        )
        action = self.kernel.resolve(ActionGate).propose(call)
        return redact_emails(
            f"Not run: `{call.command}` changes things, so it waits for the person's confirmation in the chat "
            f"(action {action}). Tell them briefly what it will do and why, then stop; don't call it again."
        )

    def _confirm(self, user: CurrentUser, arguments: Mapping[str, Any]) -> object:
        if arguments.get("session") != self.session:
            msg = "This confirmation is limited to the MCP chat session"
            raise ChatToolError(msg)
        try:
            with user.local(AUTHENTICATION):
                mark_authenticated_presence(self.kernel, self.item.record.qualname)
                return self.kernel.invoke(self.item.operation, **arguments)
        except HeinzelError:
            raise
        except Exception as exc:
            log_event(log, "mcp.chat_confirmation.failed", level=logging.ERROR, exc_info=True, session=self.session)
            msg = "The authorized confirmation failed; no approval token was returned"
            raise ChatToolError(msg) from exc


def _pre_parsed(arguments: dict[str, Any], model: type[BaseModel]) -> dict[str, Any]:
    """Parse a list or object an agent sent as a JSON string, as the MCP SDK does, so both paths read it alike.

    Decided on the model's field, as the SDK does, so `Annotated[str, Field(...)]` counts as a string and keeps its
    value; never to a bare string or number either: `"5"` stays as it came.
    """
    parsed = dict(arguments)
    for name, value in arguments.items():
        field = model.model_fields.get(name)
        if field is None or not isinstance(value, str) or field.annotation is str:
            continue
        try:
            decoded = json.loads(value)
        except (ValueError, RecursionError):
            continue
        if not isinstance(decoded, str | int | float):
            parsed[name] = decoded
    return parsed


def _relayed(answer: Mapping[str, Any]) -> object:
    """What the process with the desktop answered: the tool's text or picture.

    Raises:
        ChatToolError: If it refused the call, or answered something else.
    """
    text, image = answer.get("text"), answer.get("image")
    if isinstance(text, str):
        return text
    if isinstance(image, dict):
        return PageImage.model_validate(image)
    error = answer.get("error")
    raise ChatToolError(redact_emails(error) if isinstance(error, str) else "The browser call failed in hz.")


def _result_text(result: object) -> str:
    """A result as the agent gets it: JSON without email addresses, cut at the limit."""
    # Redacted before it is cut, so no address is cut into something the pattern misses.
    text = redact_emails(json.dumps(to_jsonable_python(result, fallback=str), ensure_ascii=False))
    return text[:RESULT_LIMIT] + ("…(cut)" if len(text) > RESULT_LIMIT else "")


def chat_tools(kernel: Kernel, session: str, tools: ChatToolSet = "everyday") -> list[ChatTool]:
    """The tools of one chat session's agent, on whichever path the turn calls them.

    The session must exist; the agent cannot choose it. Private operations are left out (the durable engine grants
    private data, never ambient chat tools) unless the profile is a check-in's, whose only tool is its proposal.

    Raises:
        HeinzelError: If the session does not exist, or the check-in profile is asked for another chat.
    """
    user = kernel.resolve(CurrentUser)
    with user.local_read(AUTHENTICATION):
        sessions = kernel.resolve(McpChatSessions)
        sessions.require(session)
        if tools == "personal-check-in":
            if not sessions.is_check_in(session):
                msg = "The personal-check-in tool profile requires a durable check-in chat"
                raise HeinzelError(msg)
            return [
                ChatTool(kernel, item, session)
                for item in exposed(kernel, "mcp-check-in")
                if item.record.qualname == "heinzel" and item.operation.name == "propose-check-in"
            ]
    # Built once here, before a turn calls tools from several threads: `Kernel.resolve` takes no lock.
    for service in (ActionGate, BrowserBridge):
        if kernel.provides(service):
            kernel.resolve(service)
    selected: list[ChatTool] = []
    for item in exposed(kernel, HEAD):
        if (item.record.qualname, item.operation.name) == CONFIRM_AUTHORIZED:
            selected.append(ChatTool(kernel, item, session, "confirm-authorized"))
        elif not item.operation.private and (tools == "all" or item.operation.audience == "everyday"):
            selected.append(ChatTool(kernel, item, session))
    return selected


def direct_tools(kernel: Kernel, profile: ToolProfile) -> tuple[ToolProfile, list[ChatTool]]:
    """Split a turn's profile for a runner that calls hz in-process: the profile without hz's pipe, and hz's tools.

    Without `hz_tools` the profile is returned as it is, with no tools: whatever is in `mcp_servers` is a server
    the runner starts itself.

    Raises:
        HeinzelError: If the chat session does not exist, or the check-in profile is asked for another chat.
    """
    hz = profile.hz_tools
    if hz is None:
        return profile, []
    tools = chat_tools(kernel, hz.session, hz.tools)
    servers = {name: server for name, server in profile.mcp_servers.items() if name != hz.server}
    return profile.model_copy(update={"mcp_servers": servers, "hz_tools": None}), tools


def tool_name(server: str, tool: str) -> str:
    """A tool's name among a turn's sources, `mcp__<server>__<tool>`, as an agent CLI names MCP tools."""
    return f"mcp__{server}__{tool}"


def direct_result(tool: ChatTool, arguments: Mapping[str, Any]) -> str:
    """One in-process call as text for a model: what the MCP pipe would answer, refusals as `Error: ...`."""
    try:
        result = tool.call(arguments)
    except ChatToolError as exc:
        return f"Error: {exc}"
    if isinstance(result, PageImage):
        return json.dumps(result.caption(), ensure_ascii=False)
    return result if isinstance(result, str) else json.dumps(to_jsonable_python(result, fallback=str))


def direct_picture(tool: ChatTool, arguments: Mapping[str, Any]) -> object:
    """Like `direct_result`, but a picture of a browser tab reaches the model as an image beside its caption."""
    from pydantic_ai import BinaryContent, ToolReturn  # noqa: PLC0415 - only an in-process turn needs it

    try:
        result = tool.call(arguments)
    except ChatToolError as exc:
        return f"Error: {exc}"
    if isinstance(result, PageImage):
        caption = json.dumps(result.caption(), ensure_ascii=False)
        picture = BinaryContent(data=base64.b64decode(result.data), media_type=result.media_type)
        return ToolReturn(return_value=caption, content=[caption, picture])
    return result if isinstance(result, str) else json.dumps(to_jsonable_python(result, fallback=str))


def pydantic_ai_tools(
    tools: list[ChatTool], *, counted: Callable[[str], None] | None = None, server: str = SERVER
) -> "list[Tool[Any]]":
    """The chat's tools as Pydantic AI tools for a turn that runs its model inside hz: no process, no MCP hop.

    Each runs on a worker thread (Pydantic AI's way with plain functions), so a slow operation never blocks the
    turn's event loop; `counted` hears each call's name.
    """
    from pydantic_ai import Tool  # noqa: PLC0415 - only a turn that runs its model in-process needs it

    def wrap(tool: ChatTool, name: str) -> Callable[..., object]:
        def call(**arguments: Any) -> object:  # noqa: ANN401 - the model's arguments, validated by the schema
            if counted is not None:
                counted(name)
            if tool.item.returns is PageImage:
                return direct_picture(tool, arguments)
            return direct_result(tool, arguments)

        return call

    wrapped: list[Tool[Any]] = []
    for tool in tools:
        name = tool_name(server, tool.name)
        wrapped.append(Tool.from_schema(wrap(tool, name), name, tool.description, tool.schema()))
    return wrapped
