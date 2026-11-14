"""A Heinzel's questions also in chat (ADR 0160): the races, the caps, the notes, and what an answer may become."""

import hashlib
import threading
import time
from datetime import UTC, datetime

import pytest

from heinzel import CurrentUser, HeinzelError, SelfAnswer, SelfMessage
from heinzel.kernel._self_chat import SelfChat
from heinzel.modules.heinzel import _self_chat_questions as relay_module
from heinzel.modules.heinzel._self_chat_questions import (
    ANSWERED_HERE,
    NOT_USED,
    REFUSED,
    STILL_OPEN,
    QuestionRelay,
    QuestionSettings,
)
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import QUESTION_ASKED, MessageRequest, SessionCosts, SessionMessage
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit


class Inbox:
    """The owner's own direct messages: `answers` says what the owner does with each question, in order."""

    def __init__(self, *answers: object, ignore_cancel: bool = False) -> None:
        self.answers = list(answers)
        self.ignore_cancel = ignore_cancel
        self.asked: list[dict[str, object]] = []
        self.notes: list[tuple[str, str | None]] = []
        self.waiting = threading.Event()
        self.release = threading.Event()

    def notify_self(self, text: str, *, thread: str | None = None, context: str | None = None) -> SelfMessage:
        del context
        self.notes.append((text, thread))
        return SelfMessage(id=f"n{len(self.notes)}", thread=thread or "t-new")

    def ask_self(  # noqa: PLR0913 - the protocol's shape
        self,
        question: str,
        *,
        options=(),
        timeout: float,
        thread: str | None = None,
        context: str | None = None,
        cancel: threading.Event | None = None,
    ) -> SelfAnswer:
        self.asked.append({"question": question, "thread": thread, "context": context, "timeout": timeout})
        answer = self.answers.pop(0) if self.answers else "wait"
        if answer == "wait":  # the owner looks elsewhere until cancelled, released or out of time
            self.waiting.set()
            deadline = time.monotonic() + (60 if self.ignore_cancel else timeout)  # a hung adapter overruns
            while time.monotonic() < deadline and not self.release.is_set():
                if cancel is not None and cancel.is_set() and not self.ignore_cancel:
                    return SelfAnswer(timed_out=True, thread="t-1")
                time.sleep(0.01)
            answer = self.answers.pop(0) if self.answers else SelfAnswer(timed_out=True, thread="t-1")
        if isinstance(answer, Exception):
            raise answer
        assert isinstance(answer, SelfAnswer)
        return answer


class Chat:
    """Stands in for the chat service: records the turns an answer from chat runs."""

    def __init__(self) -> None:
        self.turns: list[tuple[str, str]] = []

    async def answer_from_chat(self, session: str, request: str) -> None:
        self.turns.append((session, request))


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(relay_module, "POLL", 0.02)
    monkeypatch.setattr(relay_module, "GRACE", 0.3)
    QuestionRelay._live.clear()


@pytest.fixture
def world(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        costs = SessionCosts(session_max_usd=2, message_max_usd=0.2)
        crud = SessionCrud(PersonalSessionRepository(current), lambda: costs)
        session = crud.new_chat("new").session_id
    return current, crud, session


def _ask(crud: SessionCrud, session: str, turn: str, text: str = "Which calendar?", *, question: bool = True) -> int:
    """Record a reply of `turn`, as a finished turn does; with a question, the event that marks it."""
    reply = SessionMessage(
        id="reply-" + hashlib.sha256(turn.encode()).hexdigest(),
        seq=1,
        turn_id=turn,
        role="assistant",
        text=text,
        at=datetime.now(UTC),
    )
    with crud.repository.control_writer(session) as writer:
        body = immutable_body(writer, "reply", reply.model_dump(mode="json"), control=True)
        event = writer.append(
            "message.replied", key=turn, turn=turn, data={"message_id": reply.id, **body.model_dump(mode="json")}
        )
        if question:
            writer.append(QUESTION_ASKED, key=f"question-{turn}", turn=turn, data={"message_id": reply.id})
    return event.seq


def _relay(world, inbox: Inbox, chat: Chat, **settings: object) -> QuestionRelay:
    current, crud, _session = world
    values = {"chat_questions": True, "chat_wait_minutes": 1} | settings
    channel = SelfChat(adapter="acmechat", notifies=True, asks=True, build=lambda: inbox)
    return QuestionRelay(
        chat,  # type: ignore[arg-type]
        crud,
        current,
        lambda: channel,
        current.directory.parent,
        settings=lambda: QuestionSettings.model_validate(values),
    )


def _run(relay: QuestionRelay, session: str, turn: str) -> None:
    relay.asked(session, turn)
    for worker in relay.threads:
        worker.join(10)
        assert not worker.is_alive()


def _accepted(crud: SessionCrud, session: str) -> list[dict]:
    with crud.repository.current.local("cli"):
        return [e.data for e in crud.repository.control_read(session).events if e.kind == "message.accepted"]


def test_an_answer_in_chat_first_becomes_the_owners_next_message(world):
    current, crud, session = world
    inbox, chat = Inbox(SelfAnswer(text="The work one", thread="t-1")), Chat()
    with current.local("cli"):
        _ask(crud, session, "turn-1")
    _run(_relay(world, inbox, chat), session, "turn-1")
    assert inbox.asked[0]["question"] == "Which calendar?"
    assert inbox.asked[0]["context"] == "New Heinzel"
    assert inbox.asked[0]["timeout"] == 60
    accepted = _accepted(crud, session)
    assert [a["origin"] for a in accepted] == ["self-chat"]
    with current.local("cli"):
        message = crud.read_message(session, crud.repository.control_read(session).events[-1])
    assert (message.text, message.origin, message.role) == ("The work one", "self-chat", "user")
    assert chat.turns == [(session, accepted[0]["message_id"])]
    assert inbox.notes == [("Got it: your answer is with New Heinzel.", "t-1")]


def test_an_answer_in_the_app_first_cancels_the_chat_ask_and_says_so(world):
    current, crud, session = world
    inbox, chat = Inbox("wait"), Chat()
    with current.local("cli"):
        _ask(crud, session, "turn-1")
    relay = _relay(world, inbox, chat)
    relay.asked(session, "turn-1")
    assert inbox.waiting.wait(5)
    with current.local("cli"):
        crud.accept(session, MessageRequest(request_id="typed", text="The work one"))
    for worker in relay.threads:
        worker.join(10)
    assert chat.turns == []
    assert [a["origin"] for a in _accepted(crud, session)] == ["cli"]
    assert inbox.notes == [(ANSWERED_HERE, "t-1")]


def test_a_late_chat_answer_after_an_app_answer_is_dropped_by_the_compare_and_set(world):
    current, crud, session = world
    inbox, chat = Inbox("wait", SelfAnswer(text="Late"), ignore_cancel=True), Chat()
    with current.local("cli"):
        _ask(crud, session, "turn-1")
    relay = _relay(world, inbox, chat)
    relay.asked(session, "turn-1")
    assert inbox.waiting.wait(5)
    with current.local("cli"):
        crud.accept(session, MessageRequest(request_id="typed", text="Typed"))
    time.sleep(0.1)
    inbox.release.set()
    for worker in relay.threads:
        worker.join(10)
    assert [a["origin"] for a in _accepted(crud, session)] == ["cli"], "the late chat answer never lands"
    assert chat.turns == []
    assert inbox.notes == [(ANSWERED_HERE, None)], "judged by the relay's own record, not the adapter"


def test_only_one_answer_wins_when_both_arrive_at_once(world):
    current, crud, session = world
    with current.local("cli"):
        seq = _ask(crud, session, "turn-1")
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def typed() -> None:
        with current.local("cli"):
            barrier.wait()
            results["typed"] = crud.accept(session, MessageRequest(request_id="typed", text="Typed"))

    def from_chat() -> None:
        with current.local("self-chat"):
            barrier.wait()
            results["chat"] = crud.accept_answer(session, MessageRequest(request_id="chat", text="Chat"), seq)

    workers = [threading.Thread(target=typed), threading.Thread(target=from_chat)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    accepted = _accepted(crud, session)
    if results["chat"] is None:
        assert [a["origin"] for a in accepted] == ["cli"]
    else:
        assert [a["origin"] for a in accepted] == ["self-chat", "cli"], "the typed one is a follow-up"


def test_no_answer_in_time_leaves_the_question_open_in_the_app(world):
    current, crud, session = world
    inbox, chat = Inbox(SelfAnswer(timed_out=True, thread="t-1")), Chat()
    with current.local("cli"):
        _ask(crud, session, "turn-1")
    _run(_relay(world, inbox, chat), session, "turn-1")
    assert _accepted(crud, session) == []
    assert inbox.notes == [(STILL_OPEN, "t-1")]


def test_an_adapter_that_ignores_cancel_and_hangs_frees_its_place(world):
    current, crud, session = world
    inbox, chat = Inbox(ignore_cancel=True), Chat()
    with current.local("cli"):
        _ask(crud, session, "turn-1")
    relay = _relay(world, inbox, chat)
    channel = SelfChat(adapter="acmechat", notifies=True, asks=True, build=lambda: inbox)
    with current.local("self-chat"):
        ask = relay._register(session, "q", 1)
        assert ask is not None
        started = time.monotonic()
        answer, problem = relay._wait(channel, ask, ("Which?", "Ada", None), 1)
    assert (answer, problem) == (None, "abandoned")
    assert time.monotonic() - started < 3
    inbox.release.set()


def test_a_refused_answer_leaves_the_question_open(world):
    current, crud, session = world
    inbox, chat = Inbox(SelfAnswer(option="Delete everything", text="x")), Chat()
    with current.local("cli"):
        _ask(crud, session, "turn-1")
    _run(_relay(world, inbox, chat), session, "turn-1")
    assert inbox.notes == [(REFUSED, None)], "an option that wasn't offered is refused"
    assert _accepted(crud, session) == []


def test_an_answer_to_an_old_question_is_not_used(world):
    current, crud, session = world
    inbox, chat = Inbox(SelfAnswer(text="Late")), Chat()
    with current.local("cli"):
        seq = _ask(crud, session, "turn-1")
        crud.accept(session, MessageRequest(request_id="typed", text="Typed"))
        assert crud.accept_answer(session, MessageRequest(request_id="c", text="x"), seq) is None
    relay = _relay(world, inbox, chat)
    with current.local("self-chat"):
        ask = relay._register(session, "q", seq)
        assert ask is not None
        channel = SelfChat("acmechat", notifies=True, asks=True, build=lambda: inbox)
        relay._settle(channel, ask, (SelfAnswer(text="Late"), None), (None, "Ada"))
    assert inbox.notes == [(NOT_USED, None)]


def test_routing_uses_the_relays_record_not_the_adapters_thread(world):
    current, crud, session = world
    with current.local("cli"):
        other = crud.new_chat("Bea").session_id
        _ask(crud, session, "turn-1")
    inbox, chat = Inbox(SelfAnswer(text="Mine", thread=f"thread-of-{other}")), Chat()
    _run(_relay(world, inbox, chat), session, "turn-1")
    assert [a["origin"] for a in _accepted(crud, session)] == ["self-chat"]
    assert _accepted(crud, other) == []


def test_one_live_ask_per_chat_and_a_small_cap(world):
    current, crud, session = world
    relay = _relay(world, Inbox(), Chat())
    first = relay._register(session, "q1", 1)
    second = relay._register(session, "q2", 2)
    assert first is not None
    assert second is not None
    assert first.ended == "superseded", "a newer question supersedes the older ask"
    assert first.cancel.is_set()
    with current.local("cli"):
        others = [crud.new_chat(f"H{n}").session_id for n in range(3)]
    assert relay._register(others[0], "a", 1) is not None
    assert relay._register(others[1], "b", 1) is not None
    assert relay._register(others[2], "c", 1) is None, "over the cap the question is only in the app"
    relay._unregister(second)
    assert relay._register(others[2], "c", 1) is not None


def test_a_superseded_ask_says_nothing(world):
    current, crud, session = world
    inbox, chat = Inbox("wait"), Chat()
    with current.local("cli"):
        _ask(crud, session, "turn-1")
    relay = _relay(world, inbox, chat)
    relay.asked(session, "turn-1")
    assert inbox.waiting.wait(5)
    relay._register(session, "newer", 99)
    for worker in relay.threads:
        worker.join(10)
    assert inbox.notes == []


def test_off_quiet_or_unsupported_asks_nothing(world):
    current, crud, session = world
    with current.local("cli"):
        _ask(crud, session, "turn-1")
    for settings in ({"chat_questions": False}, {"quiet": True}, {"enabled": False}):
        inbox = Inbox(SelfAnswer(text="x"))
        _run(_relay(world, inbox, Chat(), **settings), session, "turn-1")
        assert inbox.asked == []
    inbox = Inbox(SelfAnswer(text="x"))
    relay = _relay(world, inbox, Chat())
    relay._self_chat = lambda: SelfChat("acmechat", notifies=True, asks=False, build=lambda: inbox)
    _run(relay, session, "turn-1")
    assert inbox.asked == []


def test_a_reply_that_asks_nothing_is_not_asked(world):
    current, crud, session = world
    with current.local("cli"):
        _ask(crud, session, "turn-1", "Done.", question=False)
    inbox = Inbox(SelfAnswer(text="x"))
    _run(_relay(world, inbox, Chat()), session, "turn-1")
    assert inbox.asked == []


def test_the_thread_is_kept_per_chat_across_relays(world):
    current, crud, session = world
    with current.local("cli"):
        _ask(crud, session, "turn-1")
    _run(_relay(world, Inbox(SelfAnswer(timed_out=True, thread="t-ada")), Chat()), session, "turn-1")
    with current.local("cli"):
        _ask(crud, session, "turn-2", "And the time?")
    inbox = Inbox(SelfAnswer(timed_out=True, thread="t-other"))
    _run(_relay(world, inbox, Chat()), session, "turn-2")
    assert inbox.asked[0]["thread"] == "t-ada", "a new relay (a restart) finds the chat's thread"
    assert inbox.notes == [(STILL_OPEN, "t-ada")]


def test_asked_returns_at_once_and_never_blocks_the_caller(world):
    current, crud, session = world
    inbox = Inbox("wait")
    with current.local("cli"):
        _ask(crud, session, "turn-1")
    relay = _relay(world, inbox, Chat())
    started = time.monotonic()
    relay.asked(session, "turn-1")
    assert time.monotonic() - started < 0.2
    assert inbox.waiting.wait(5)
    inbox.release.set()
    for worker in relay.threads:
        worker.join(10)


def test_a_failing_self_chat_costs_nothing(world):
    current, crud, session = world
    with current.local("cli"):
        _ask(crud, session, "turn-1")
    relay = _relay(world, Inbox(), Chat())

    def broken() -> SelfChat:
        msg = "no adapter"
        raise HeinzelError(msg)

    relay._self_chat = broken
    _run(relay, session, "turn-1")
    assert QuestionRelay._live == {}


def test_an_ask_a_restart_left_waiting_is_said_to_be_still_open_once(world):
    current, crud, session = world
    with current.local("cli"):
        other = crud.new_chat("other").session_id
    relay = _relay(world, Inbox(), Chat())
    with relay.ledger.locked() as state:
        state["threads"][session] = "t-old"
        state["live"][session] = "999999999"  # no such process: it ended while it waited
        state["live"][other] = str(__import__("os").getpid())  # this process: still waiting
    with current.local("cli"):
        _ask(crud, session, "turn-1")
    inbox = Inbox(SelfAnswer(timed_out=True, thread="t-old"))
    _run(_relay(world, inbox, Chat()), session, "turn-1")
    assert inbox.notes[0] == (STILL_OPEN, "t-old")
    with relay.ledger.locked() as state:
        assert session not in state["live"]
        assert other in state["live"]
    inbox = Inbox(SelfAnswer(timed_out=True, thread="t-old"))
    _run(_relay(world, inbox, Chat()), session, "turn-1")
    assert inbox.notes == [(STILL_OPEN, "t-old")], "said once, then only this ask's own note"


def test_the_self_chat_identity_never_approves():
    """Approval, publication and head-only paths allowlist heads or the daemon; only these files may name self-chat."""
    from pathlib import Path

    allowed = {
        "kernel/_identity.py",
        "kernel/_self_chat.py",
        "modules/heinzel/_session_models.py",
        "modules/heinzel/_user_chat.py",
        "modules/heinzel/_self_chat_questions.py",
    }
    root = Path(relay_module.__file__).resolve().parents[2]
    naming = {
        str(path.relative_to(root)) for path in root.rglob("*.py") if '"self-chat"' in path.read_text(encoding="utf-8")
    }
    assert naming <= allowed


def _delete(crud: SessionCrud, monkeypatch, session: str) -> None:
    """Core has no delete for a Heinzel chat yet: one that is gone is one the repository no longer lists."""
    heads = crud.repository.heads
    monkeypatch.setattr(crud.repository, "heads", lambda: [h for h in heads() if h.session != session])


def test_a_deleted_chats_thread_is_forgotten_and_never_reused(world, monkeypatch):
    current, crud, old = world
    forgotten: list[set[str]] = []
    relay = _relay(world, Inbox(), Chat())
    relay._forget_notices = forgotten.append
    with relay.ledger.locked() as state:
        state["threads"][old] = "t-old"
        state["live"][old] = "999999999"
    _delete(crud, monkeypatch, old)
    with current.local("cli"):
        new = crud.new_chat("again").session_id
        _ask(crud, new, "turn-1")
    inbox = Inbox(SelfAnswer(timed_out=True, thread="t-new"))
    relay._self_chat = lambda: SelfChat(adapter="acmechat", notifies=True, asks=True, build=lambda: inbox)
    _run(relay, new, "turn-1")
    assert new != old
    assert inbox.asked[0]["thread"] is None, "a new chat starts its own thread"
    assert inbox.notes == [(STILL_OPEN, "t-new")], "the new chat's own thread; no note for the deleted chat's ask"
    with relay.ledger.locked() as state:
        assert old not in state["threads"]
        assert old not in state["live"]
    assert forgotten == [{new}], "the notices' threads of gone chats are dropped too"


def test_a_live_ask_of_a_deleted_chat_is_cancelled_silently(world, monkeypatch):
    current, crud, session = world
    inbox = Inbox("wait")
    with current.local("cli"):
        _ask(crud, session, "turn-1")
        other = crud.new_chat("other").session_id
        _ask(crud, other, "turn-2")
    relay = _relay(world, inbox, Chat())
    relay.asked(session, "turn-1")
    assert inbox.waiting.wait(5)
    _delete(crud, monkeypatch, session)
    inbox.answers.append(SelfAnswer(timed_out=True))
    relay.asked(other, "turn-2")
    for worker in relay.threads:
        worker.join(10)
        assert not worker.is_alive()
    assert [a["question"] for a in inbox.asked] == ["Which calendar?", "Which calendar?"]
    assert inbox.notes == [(STILL_OPEN, None)], "only the other chat's own note; nothing for the deleted chat"
    assert QuestionRelay._live == {}


def test_tidying_that_fails_never_costs_the_question(world, caplog):
    current, crud, session = world
    with current.local("cli"):
        _ask(crud, session, "turn-1")
    inbox = Inbox(SelfAnswer(timed_out=True, thread="t-1"))
    relay = _relay(world, inbox, Chat())

    def broken(chats: set[str]) -> None:
        msg = "no notify module"
        raise HeinzelError(msg)

    relay._forget_notices = broken
    _run(relay, session, "turn-1")
    assert len(inbox.asked) == 1
    assert "self_chat.forget.failed" in caplog.text
