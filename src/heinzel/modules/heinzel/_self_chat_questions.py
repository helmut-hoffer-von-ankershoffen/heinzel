"""Questions also in chat: a Heinzel's question asked in the owner's own direct messages too, if switched on (ADR 0160).

A Heinzel asks by ending its turn with a question: the reply in its chat, marked by a `question.asked` event. With
`[system.notify] chat_questions` on and a bound chat adapter that serves `ask_self`, the relay also asks it there, in a
worker thread, never on the event loop, with the owner's own wait (`chat_wait_minutes`). The app shows the question as
before; whichever answer comes first counts:

- An answer in the app first (any message after the question): the relay cancels the chat ask and says in the thread
  that it was answered in Heinzel. A chat answer that still arrives is dropped.
- An answer in chat first: it becomes the owner's next message in that chat, exactly as if typed (same validation, same
  turn), accepted by one compare-and-set against anything else arriving; its origin is `self-chat`, which no approval
  path accepts. A chosen option must be one that was offered; it is then exactly that label.
- No answer in time: the thread says the question is still open in Heinzel, which is where it stays, as in the app.
- The process ended while it waited (a restart): the next ask in any chat says so in that thread: still open.

Answers are routed by the relay's own record (the chat, the question it asked), never by a thread the adapter returns.
Write approvals never go this way (ADR 0068, 0120): only questions. One live ask per Heinzel chat (a newer question
supersedes it) and at most `MAX_LIVE` in this process; over the cap, the question is only in the app. An adapter that
ignores `cancel` frees its place `GRACE` seconds after the wait.
"""

import asyncio
import hashlib
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from heinzel import Config, CurrentUser, HeinzelError, SelfAnswer, clean_text
from heinzel.kernel._logging import get_logger, log_event
from heinzel.kernel._self_chat import STATE, SelfChat, SelfChatLedger
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import QUESTION_ASKED, MessageRequest
from heinzel.modules.system.notify import NotifySettings

if TYPE_CHECKING:
    from heinzel.modules.heinzel._user_chat import UserChatService

_EVENTS = get_logger("heinzel.self_chat")
MAX_LIVE = 3
"""Most chat asks waiting at once in this process."""
GRACE = 30.0
"""Seconds an adapter may overrun its wait before the relay stops waiting for it."""
POLL = 2.0
"""Seconds between looks at the chat for an answer typed in the app."""
ANSWERED_HERE = "Answered in Heinzel."
STILL_OPEN = "No answer here in time: the question is still open in Heinzel."
NOT_USED = "This was already answered in Heinzel, so this reply wasn't used."
REFUSED = "That answer couldn't be used; the question is still open in Heinzel."

End = Literal["answered-here", "superseded"]


@dataclass(eq=False)
class _Ask:
    """One live chat ask: the relay's own record of which chat and question it belongs to."""

    session: str
    question: str
    seq: int
    """Where the question is in the chat's control journal: anything after it answers it."""
    cancel: threading.Event = field(default_factory=threading.Event)
    ended: End | None = None


class QuestionRelay:
    """Asks a Heinzel's question in chat too, and brings a chat answer back as the owner's message."""

    _live: dict[str, _Ask] = {}  # noqa: RUF012 - one registry per process: the caps are per process
    _guard = threading.Lock()

    def __init__(  # noqa: PLR0913 - the trusted host seams, all explicit
        self,
        chat: "UserChatService",
        crud: SessionCrud,
        current: CurrentUser,
        self_chat: Callable[[], SelfChat | None],
        state_dir: Path,
        *,
        settings: Callable[[], NotifySettings] | None = None,
    ) -> None:
        """Bind the chat service that runs turns, the owner's sessions, and the self channel."""
        self._chat, self._crud, self._current, self._self_chat = chat, crud, current, self_chat
        self.ledger = SelfChatLedger(state_dir / STATE, "questions", ("threads", "live"))
        self._settings = settings or (lambda: Config.load(Path.home()).settings(NotifySettings, "system.notify"))
        self.threads: list[threading.Thread] = []
        """Workers started, for tests to join."""

    def asked(self, session: str, turn: str) -> None:
        """A turn of `session` ended with a question: ask it in chat too, in the background. Returns at once."""
        worker = threading.Thread(target=self._guarded, args=(session, turn), name="hz-self-chat-ask", daemon=True)
        self.threads.append(worker)
        worker.start()

    def _guarded(self, session: str, turn: str) -> None:
        try:
            with self._current.local("self-chat"):
                self.relay(session, turn)
        except Exception as exc:  # noqa: BLE001 - isolation boundary: chat never costs the question in the app
            log_event(_EVENTS, "self_chat.question.failed", level=logging.WARNING, problem=type(exc).__name__)

    def relay(self, session: str, turn: str) -> None:
        """Ask the question of `turn` in chat and act on what comes first; under the `self-chat` identity."""
        settings = self._settings()
        if not settings.chat_questions or settings.quiet or not settings.enabled:
            return
        channel = self._self_chat()
        if channel is None or not channel.asks:
            return
        self._close_leftovers(channel)
        found = self._question(session, turn)
        if found is None:
            return
        question, seq, text = found
        ask = self._register(session, question, seq)
        if ask is None:
            log_event(_EVENTS, "self_chat.question.over_cap", level=logging.INFO, live=MAX_LIVE)
            return
        mine = str(os.getpid())
        try:
            name = self._crud.snapshot(session, limit=1).name
            with self.ledger.locked() as state:
                thread = state["threads"].get(session)
                state["live"][session] = mine
            answer, problem = self._wait(channel, ask, (text, name, thread), settings.chat_wait_minutes * 60.0)
        finally:
            self._unregister(ask)
            with self.ledger.locked() as state:
                if state["live"].get(session) == mine:
                    del state["live"][session]
        thread = self._remember(session, thread, answer)
        self._settle(channel, ask, (answer, problem), (thread, name))

    def _close_leftovers(self, channel: SelfChat) -> None:
        """Say "still open" in the threads of asks a process that ended (a restart) left waiting; once each."""
        with self.ledger.locked() as state:
            ended = [(s, state["threads"].get(s)) for s, pid in state["live"].items() if not _alive(pid)]
            for session, _thread in ended:
                del state["live"][session]
        for session, thread in ended:
            try:
                name = self._crud.snapshot(session, limit=1).name
            except HeinzelError:
                continue  # the chat is gone
            self._note(channel, STILL_OPEN, thread, name)

    def _question(self, session: str, turn: str) -> tuple[str, int, str] | None:
        """The question `turn` asked: its message id, its event's position and its text; None if it asked none."""
        events = self._crud.repository.control_read(session).events
        asked = next((e for e in events if e.kind == QUESTION_ASKED and e.key == f"question-{turn}"), None)
        reply = next((e for e in events if e.kind == "message.replied" and e.key == turn), None)
        if asked is None or reply is None or asked.data.get("message_id") != reply.data.get("message_id"):
            return None
        message = self._crud.read_message(session, reply)
        return message.id, reply.seq, message.text

    def _register(self, session: str, question: str, seq: int) -> _Ask | None:
        """Make this the chat's one live ask, superseding an older one; None when the process is at its cap."""
        with self._guard:
            older = self._live.get(session)
            if older is not None:
                older.ended = "superseded"
                older.cancel.set()
                del self._live[session]
            if len(self._live) >= MAX_LIVE:
                return None
            ask = _Ask(session, question, seq)
            self._live[session] = ask
            return ask

    def _unregister(self, ask: _Ask) -> None:
        with self._guard:
            if self._live.get(ask.session) is ask:
                del self._live[ask.session]

    def _wait(
        self, channel: SelfChat, ask: _Ask, asked: tuple[str, str, str | None], timeout: float
    ) -> tuple[SelfAnswer | None, str | None]:
        """Ask `(text, context, thread)` and wait, watching the app for an answer; the answer, or why there is none."""
        text, name, thread = asked
        outcome: list[SelfAnswer | BaseException] = []

        def call() -> None:
            try:
                outcome.append(channel.ask(text, timeout=timeout, thread=thread, context=name, cancel=ask.cancel))
            except Exception as exc:  # noqa: BLE001 - reported below as no answer
                outcome.append(exc)

        caller = threading.Thread(target=call, name="hz-self-chat-adapter", daemon=True)
        caller.start()
        waited = 0.0
        while caller.is_alive() and waited < timeout + GRACE:
            caller.join(POLL)
            waited += POLL
            if ask.ended is None and self.answered_after(ask.session, ask.seq):
                ask.ended = "answered-here"
                ask.cancel.set()
        if not outcome:
            return None, "abandoned"
        result = outcome[0]
        if isinstance(result, BaseException):
            log_event(_EVENTS, "self_chat.question.refused", level=logging.WARNING, problem=type(result).__name__)
            return None, "refused"
        return result, None

    def answered_after(self, session: str, seq: int) -> bool:
        """Whether anything came after the question at `seq`: an answer in a head, or a newer turn."""
        events = self._crud.repository.control_read(session).events
        return any(e.seq > seq and e.kind in {"message.accepted", "message.replied"} for e in events)

    def _remember(self, session: str, thread: str | None, answer: SelfAnswer | None) -> str | None:
        """Keep the chat's thread for its next messages; only a first thread is taken from the adapter."""
        if thread is not None or answer is None or answer.thread is None:
            return thread
        with self.ledger.locked() as state:
            return state["threads"].setdefault(session, answer.thread)

    def _settle(
        self,
        channel: SelfChat,
        ask: _Ask,
        result: tuple[SelfAnswer | None, str | None],
        where: tuple[str | None, str],
    ) -> None:
        """Act on how the ask ended, judged by the relay's own record first, then by the adapter's answer."""
        answer, problem = result
        thread, name = where
        if ask.ended == "superseded":
            return
        if ask.ended == "answered-here":
            self._note(channel, ANSWERED_HERE, thread, name)
            return
        if problem == "refused":
            self._note(channel, REFUSED, thread, name)
            return
        if answer is None or answer.timed_out:
            self._note(
                channel, ANSWERED_HERE if self.answered_after(ask.session, ask.seq) else STILL_OPEN, thread, name
            )
            return
        request = "self-chat-" + hashlib.sha256(f"{ask.session}\0{ask.question}".encode()).hexdigest()[:40]
        accepted = self._crud.accept_answer(ask.session, MessageRequest(request_id=request, text=answer.text), ask.seq)
        if accepted is None:
            self._note(channel, NOT_USED, thread, name)
            return
        self._note(channel, f"Got it: your answer is with {name}.", thread, name)
        asyncio.run(self._chat.answer_from_chat(ask.session, request))

    def _note(self, channel: SelfChat, text: str, thread: str | None, name: str) -> None:
        """A short follow-up in the thread, if the adapter can send one; a failure is only logged."""
        if not channel.notifies:
            return
        try:
            channel.notify(text, thread=thread, context=name)
        except Exception as exc:  # noqa: BLE001 - a follow-up note is best effort
            detail = " ".join(clean_text(str(exc)).split())[:200]
            log_event(
                _EVENTS, "self_chat.note.failed", level=logging.WARNING, problem=type(exc).__name__, detail=detail
            )


def _alive(pid: str) -> bool:
    """Whether the process `pid` still runs; an unreadable entry counts as ended."""
    if not pid.isdigit():
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
