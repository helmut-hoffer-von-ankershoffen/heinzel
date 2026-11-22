"""A Heinzel's questions also in chat (ADR 0160): the races, the caps, the notes, and what an answer may become."""

import hashlib
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from heinzel import CurrentUser, HeinzelError, SelfAnswer, SelfMessage
from heinzel.kernel.session._self_chat import SelfChat
from heinzel.modules.heinzel import _self_chat_helper as helper_module
from heinzel.modules.heinzel import _self_chat_questions as relay_module
from heinzel.modules.heinzel._self_chat_questions import (
    ANSWERED_HERE,
    HELPER,
    MAX_DETACHED,
    NOT_USED,
    REFUSED,
    STILL_OPEN,
    QuestionRelay,
    QuestionSettings,
    _Ask,
    short_lived_process,
)
from heinzel.modules.heinzel._service import HeinzelService
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import QUESTION_ASKED, MessageRequest, SessionCosts, SessionMessage
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.heinzel._user_chat import MAX_TURN_SECONDS

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


def test_the_wait_for_a_hung_adapter_is_bounded_in_real_time_on_a_slow_runner(world, monkeypatch):
    current, _crud, session = world
    inbox = Inbox(ignore_cancel=True)
    relay = _relay(world, inbox, Chat())
    channel = SelfChat(adapter="acmechat", notifies=True, asks=True, build=lambda: inbox)

    def slow(*_args):  # a slow runner: reading the app's events takes far longer than one poll
        time.sleep(0.15)
        return False

    monkeypatch.setattr(relay, "answered_after", slow)
    with current.local("self-chat"):
        ask = relay._register(session, "q", 1)
        assert ask is not None
        started = time.monotonic()
        answer, problem = relay._wait(channel, ask, ("Which?", "Ada", None), 1)
    elapsed = time.monotonic() - started
    inbox.release.set()
    assert (answer, problem) == (None, "abandoned")
    assert elapsed < 1 + 0.3 + 0.7, "the wait ends at timeout plus grace, however slow each poll is"


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
        "kernel/session/_self_chat.py",
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
    relay._forget_notices = lambda listed: forgotten.append(listed())
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

    def broken(listed: object) -> None:
        msg = "no notify module"
        raise HeinzelError(msg)

    relay._forget_notices = broken
    _run(relay, session, "turn-1")
    assert len(inbox.asked) == 1
    assert "self_chat.forget.failed" in caplog.text


class Launches:
    """Stands in for starting the detached helper: records each command and the log directory, starts nothing."""

    def __init__(self) -> None:
        self.commands: list[tuple[list[str], object, object]] = []

    def __call__(self, command: list[str], log_dir: object, cwd: object) -> object:
        self.commands.append((command, log_dir, cwd))
        return SimpleNamespace(pid=4242)


def _dead_pid() -> str:
    """A process id that just ended: started here, waited for."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(10)
    return str(child.pid)


def test_in_a_short_lived_process_a_detached_helper_asks_and_the_caller_returns_at_once(world, monkeypatch):
    current, crud, session = world
    inbox, launches = Inbox("wait"), Launches()
    monkeypatch.setattr(relay_module, "_launch", launches)
    with current.local("cli"):
        _ask(crud, session, "turn-1")
    relay = _relay(world, inbox, Chat())
    started = time.monotonic()
    with short_lived_process():
        relay.asked(session, "turn-1")
    assert time.monotonic() - started < 0.2
    assert launches.commands == [
        ([sys.executable, "-P", "-m", HELPER, session, "turn-1"], None, relay.ledger.directory)
    ], "never imports from a working directory; starts in the relay's private one"
    assert relay.threads == [], "no thread that would die with the tick"
    assert inbox.asked == [], "the helper asks, not the tick"
    relay.asked(session, "turn-1")  # outside the tick (the web app): the worker thread as before
    assert launches.commands[1:] == []
    assert inbox.waiting.wait(5)
    inbox.release.set()
    for worker in relay.threads:
        worker.join(10)


def test_the_daemon_job_marks_its_turns_short_lived(monkeypatch):
    seen: list[bool] = []

    class Schedules:
        async def tick(self, session: str, request: str, when: datetime) -> str:
            del session, request, when
            seen.append(relay_module._SHORT_LIVED.get())
            return "done"

    binding = SimpleNamespace(session_id="S1", request_id="r1", cadence_seconds=60, timeout_seconds=60)
    job = HeinzelService._autonomous_job(SimpleNamespace(), Schedules(), binding)  # type: ignore[arg-type]
    assert job.run_for_slot is not None
    assert job.run_for_slot(datetime.now(UTC)) == {"status": "completed"}
    assert seen == [True]
    assert relay_module._SHORT_LIVED.get() is False, "only inside the tick's turn"


def test_no_helper_starts_when_nothing_would_be_asked(world, monkeypatch):
    current, crud, session = world
    launches = Launches()
    monkeypatch.setattr(relay_module, "_launch", launches)
    with current.local("cli"):
        _ask(crud, session, "turn-1")
    for settings in ({"chat_questions": False}, {"quiet": True}, {"enabled": False}):
        with short_lived_process():
            _relay(world, Inbox(), Chat(), **settings).asked(session, "turn-1")
    assert launches.commands == []


def test_a_helper_that_cannot_start_costs_the_turn_nothing(world, monkeypatch, caplog):
    current, crud, session = world

    def broken(command: list[str], log_dir: object, cwd: object) -> object:
        del command, log_dir, cwd
        raise OSError

    monkeypatch.setattr(relay_module, "_launch", broken)
    with current.local("cli"):
        _ask(crud, session, "turn-1")
    with short_lived_process():
        _relay(world, Inbox(), Chat()).asked(session, "turn-1")
    assert "self_chat.helper.failed" in caplog.text


def test_detached_asks_stay_under_a_small_cap_across_processes(world, monkeypatch):
    current, crud, session = world
    launches, inbox = Launches(), Inbox(SelfAnswer(text="Work", thread="t-1"))
    monkeypatch.setattr(relay_module, "_launch", launches)
    with current.local("cli"):
        others = [crud.new_chat(f"H{n}").session_id for n in range(MAX_DETACHED)]
        _ask(crud, session, "turn-1")
    relay = _relay(world, inbox, Chat())
    with relay.ledger.locked() as state:
        for other in others:
            state["live"][other] = str(os.getpid())  # a running process asks in each
    with short_lived_process():
        relay.asked(session, "turn-1")
    assert launches.commands == [], "the tick starts no helper over the cap"
    relay.run_detached(session, "turn-1")
    assert inbox.asked == [], "a helper started meanwhile asks nothing over the cap: the question is only in the app"
    with relay.ledger.locked() as state:
        assert session not in state["live"]
        state["live"][others[0]] = _dead_pid()  # that process ended: its place is free
        state["live"][session] = str(os.getpid())  # this chat's own ask is superseded, never counted
    with short_lived_process():
        relay.asked(session, "turn-1")
    assert len(launches.commands) == 1


def test_the_helper_runs_the_same_relay_and_answer_path(world):
    current, crud, session = world
    inbox, chat = Inbox(SelfAnswer(text="The work one", thread="t-1")), Chat()
    with current.local("cli"):
        _ask(crud, session, "turn-1")
    relay = _relay(world, inbox, chat)
    relay.run_detached(session, "turn-1")
    assert [a["question"] for a in inbox.asked] == ["Which calendar?"]
    accepted = _accepted(crud, session)
    assert [a["origin"] for a in accepted] == ["self-chat"]
    with current.local("cli"):
        message = crud.read_message(session, crud.repository.control_read(session).events[-1])
    assert (message.text, message.origin, message.role) == ("The work one", "self-chat", "user")
    assert chat.turns == [(session, accepted[0]["message_id"])]
    with relay.ledger.locked() as state:
        assert state["live"] == {}, "nothing is left that a later ask would call still open"


def test_an_ask_another_process_takes_over_is_superseded_silently(world):
    current, crud, session = world
    inbox, chat = Inbox("wait"), Chat()
    with current.local("cli"):
        _ask(crud, session, "turn-1")
    relay = _relay(world, inbox, chat)
    relay.asked(session, "turn-1")
    assert inbox.waiting.wait(5)
    with relay.ledger.locked() as state:
        state["live"][session] = "1"  # a helper asks a newer question of this chat
    for worker in relay.threads:
        worker.join(10)
        assert not worker.is_alive()
    assert inbox.notes == [], "a superseded ask says nothing"
    assert chat.turns == []
    with relay.ledger.locked() as state:
        assert state["live"] == {session: "1"}, "the newer ask keeps its place"


def test_the_helper_process_starts_detached_with_its_output_in_the_log(tmp_path):
    log_dir = tmp_path / "logs"
    code = "import os, sys; sys.stdout.write('out'); sys.stderr.write(f'{os.getsid(0)} {os.getpid()}')"
    child = relay_module._launch([sys.executable, "-c", code], log_dir, tmp_path)
    try:
        assert child.wait(20) == 0
    finally:
        if child.poll() is None:
            child.kill()
    session_id, pid = (log_dir / relay_module.HELPER_LOG).read_text().split()
    assert session_id == pid, "a session of its own: the tick's end never signals it"
    assert (log_dir / relay_module.HELPER_LOG).stat().st_mode & 0o777 == 0o600


def test_a_chat_created_while_the_chats_are_listed_keeps_its_thread(world, monkeypatch):
    current, crud, _session = world
    relay = _relay(world, Inbox(), Chat())
    with relay.ledger.locked() as state:
        state["threads"]["hz-gone"] = "t-gone"
    heads = crud.repository.heads
    created: list[str] = []

    def racing() -> list:
        listed = heads()  # listed before another process creates a chat and keeps its thread
        with current.local("cli"):
            created.append(crud.new_chat("fresh").session_id)
        with relay.ledger.locked() as state:
            state["threads"][created[0]] = "t-fresh"
        return listed

    monkeypatch.setattr(crud.repository, "heads", racing)
    with current.local("self-chat"):
        relay.forget_gone()
    with relay.ledger.locked() as state:
        assert state["threads"] == {created[0]: "t-fresh"}, "only what was kept before the listing can go"


def test_a_live_ask_of_another_owner_in_this_process_is_not_cancelled(world, tmp_path):
    current, _crud, _session = world
    foreign = _Ask("hz-of-another-profile", "q", 1, tmp_path / "elsewhere" / "questions.json")
    QuestionRelay._live[foreign.ledger, foreign.session] = foreign
    relay = _relay(world, Inbox(), Chat())
    with current.local("self-chat"):
        relay.forget_gone()
    assert not foreign.cancel.is_set(), "another owner's live ask stays"
    assert QuestionRelay._live == {(foreign.ledger, foreign.session): foreign}


def test_with_the_switch_off_quiet_or_no_adapter_no_ledger_is_touched(world):
    current, crud, session = world
    with current.local("cli"):
        _ask(crud, session, "turn-1")
    notices: list[object] = []
    for settings in ({"chat_questions": False}, {"quiet": True}, {"enabled": False}):
        relay = _relay(world, Inbox(), Chat(), **settings)
        relay._forget_notices = notices.append
        _run(relay, session, "turn-1")
    relay = _relay(world, Inbox(), Chat())
    relay._forget_notices = notices.append
    relay._self_chat = lambda: None
    _run(relay, session, "turn-1")
    assert not relay.ledger.path.exists(), "off is a no-op"
    assert not relay.ledger.directory.exists()
    assert notices == [], "nor are the notices' threads looked at"


def test_tidying_with_nothing_kept_writes_nothing(world):
    current, _crud, _session = world
    relay = _relay(world, Inbox(), Chat())
    with current.local("self-chat"):
        relay.forget_gone()
    assert not relay.ledger.path.exists()


def test_an_older_ask_ending_keeps_the_newer_asks_live_entry(world):
    current, crud, session = world
    inbox = Inbox("wait", "wait")
    with current.local("cli"):
        _ask(crud, session, "turn-1")
        _ask(crud, session, "turn-2", "Which room?")
    relay = _relay(world, inbox, Chat())
    relay.asked(session, "turn-1")
    assert inbox.waiting.wait(5)
    inbox.waiting.clear()
    relay.asked(session, "turn-2")  # supersedes the first in this process
    relay.threads[0].join(10)
    assert not relay.threads[0].is_alive()
    with relay.ledger.locked() as state:
        assert state["live"] == {session: str(os.getpid())}, "the newer ask still counts as live"
    inbox.release.set()
    relay.threads[1].join(10)
    with relay.ledger.locked() as state:
        assert state["live"] == {}


def test_the_helper_never_imports_from_its_working_directory(tmp_path):
    planted = tmp_path / "cwd" / "heinzel"
    planted.mkdir(parents=True)
    marker = tmp_path / "planted-ran"
    (planted / "__init__.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n")
    child = relay_module._launch([sys.executable, "-P", "-m", HELPER], None, tmp_path / "cwd")
    try:
        child.wait(60)
    finally:
        if child.poll() is None:  # only the process this test started
            child.kill()
            child.wait(10)
    assert not marker.exists(), "a heinzel package where the helper starts never runs"


def test_the_helpers_log_starts_afresh_once_it_is_too_big(tmp_path, monkeypatch):
    monkeypatch.setattr(relay_module, "HELPER_LOG_MAX", 10)
    log = tmp_path / relay_module.HELPER_LOG
    log.write_text("x" * 11)
    child = relay_module._launch([sys.executable, "-c", "import sys; sys.stderr.write('new')"], tmp_path, tmp_path)
    try:
        assert child.wait(20) == 0
    finally:
        if child.poll() is None:
            child.kill()
    assert log.read_text() == "new"


def _helper_kernel(monkeypatch, run_detached) -> list[int]:
    """The helper's kernel, and how it ended: the codes it would `os._exit` with, in order."""
    ended: list[int] = []
    monkeypatch.setattr(helper_module, "_exit", ended.append)
    relay = SimpleNamespace(run_detached=run_detached)
    kernel = SimpleNamespace(resolve=lambda _kind: SimpleNamespace(questions=relay))
    monkeypatch.setattr(helper_module, "boot", lambda cwd: kernel)
    return ended


SESSION_ID = "hz-" + "0" * 26
TURN_ID = "interactive-" + "a" * 64


def test_a_question_the_helpers_own_answer_turn_asks_gets_a_helper_of_its_own(monkeypatch):
    seen: list[bool] = []
    ended = _helper_kernel(monkeypatch, lambda session, turn: seen.append(relay_module._SHORT_LIVED.get()))
    assert helper_module.main([SESSION_ID, TURN_ID]) == 0
    assert seen == [True]
    assert ended == [0], "done: it ends at once"


def test_the_helper_takes_only_a_chat_id_and_a_turn_id(monkeypatch):
    _helper_kernel(monkeypatch, lambda session, turn: pytest.fail("ran"))
    for arguments in ([], [SESSION_ID], [SESSION_ID, "--x"], ["S1", TURN_ID], [SESSION_ID, "turn-1"]):
        assert helper_module.main(arguments) == 2


def test_a_hung_helper_ends_at_its_lifetime(monkeypatch):
    released = threading.Event()
    monkeypatch.setattr(helper_module, "lifetime", lambda settings: 0.2)
    ended = _helper_kernel(monkeypatch, lambda session, turn: released.wait(10))  # a hung adapter, boot or answer turn

    def exit_(code: int) -> None:
        ended.append(code)
        released.set()

    monkeypatch.setattr(helper_module, "_exit", exit_)
    started = time.monotonic()
    assert helper_module.main([SESSION_ID, TURN_ID]) == 0
    assert ended[0] == helper_module.TIMED_OUT
    assert time.monotonic() - started < 5


def test_a_helpers_lifetime_is_its_wait_the_grace_one_answer_turn_and_a_margin():
    settings = QuestionSettings(chat_wait_minutes=7)
    assert relay_module.lifetime(settings) == 7 * 60 + relay_module.GRACE + MAX_TURN_SECONDS + relay_module.MARGIN


def test_a_failing_helper_reports_it_and_ends_at_once(monkeypatch, caplog):
    def broken(session: str, turn: str) -> None:
        raise RuntimeError

    ended = _helper_kernel(monkeypatch, broken)
    assert helper_module.main([SESSION_ID, TURN_ID]) == 1
    assert ended == [1]
    assert "self_chat.helper.crashed" in caplog.text
    assert "RuntimeError" in caplog.text


STRAY_THREAD = r"""
import sys, threading
from types import SimpleNamespace
from heinzel.modules.heinzel import _self_chat_helper as h
def run(session, turn):
    threading.Thread(target=threading.Event().wait).start()  # a plugin's thread that never ends, not a daemon one
relay = SimpleNamespace(run_detached=run)
h.boot = lambda cwd=None: SimpleNamespace(resolve=lambda _c: SimpleNamespace(questions=relay))
h.lifetime = lambda _s: 1.0
sys.exit(h.main([sys.argv[1], sys.argv[2]]))
"""


def test_a_helper_ends_although_a_thread_it_started_never_does(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", STRAY_THREAD, SESSION_ID, TURN_ID], cwd=tmp_path)
    try:
        code = child.wait(3.5)
    finally:
        if child.poll() is None:  # only the process this test started
            child.kill()
            child.wait(10)
    assert code == 0
