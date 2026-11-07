"""Sign-ins hz runs (ADR 0136): which grants are asked for, progress, cancel, timeout, isolation, and no secret."""

import threading
from datetime import UTC, datetime
from typing import ClassVar

import pytest

from heinzel import Document, Grant, GrantStatus, HeinzelError, Module, SignInOutcome, SignInProgress, SignInStatus
from heinzel.kernel import CapabilityView, Prompter, ScriptedPrompter
from heinzel.modules.system.connector import ConnectorService, SignInFlow, SignIns, connect
from heinzel.modules.system.connector._sign_in import wanted

pytestmark = pytest.mark.unit

READ = Grant(id="notes-read", title="Read your notes", explanation="Lets your Heinzels read notes.", default=True)
WRITE = Grant(id="notes-write", title="Change your notes", explanation="Lets your Heinzels edit notes.")
SHARE = Grant(id="notes-share", title="Share your notes", explanation="Lets your Heinzels share notes.")
PAGE = "https://accounts.notes.invalid/consent?scope=read"
OPENED: list[str] = []
NOW = datetime(2026, 11, 7, 9, 36, tzinfo=UTC)


def status(grant: Grant, state, *, needed: bool = False) -> GrantStatus:
    return GrantStatus(grant=grant, state=state, needed=needed)


@pytest.mark.parametrize(
    ("statuses", "allow", "asked"),
    [
        ([status(READ, "unknown"), status(WRITE, "unknown")], set(), {"notes-read"}),
        ([status(READ, "granted"), status(WRITE, "granted")], set(), {"notes-read", "notes-write"}),
        ([status(READ, "missing"), status(WRITE, "unknown")], {"notes-write"}, {"notes-write"}),
        (
            [status(READ, "missing", needed=True), status(SHARE, "missing", needed=True)],
            set(),
            {"notes-read", "notes-share"},
        ),
        ([status(READ, "granted"), status(WRITE, "missing")], {"notes-write"}, {"notes-read", "notes-write"}),
    ],
)
def test_a_sign_in_keeps_what_holds_or_is_needed_and_adds_only_what_is_allowed(statuses, allow, asked):
    assert wanted(statuses, frozenset(allow)) == asked


class Notes:
    def read(self, ref: str) -> Document:
        return Document(ref=ref, title=ref, text="body")


class Fake:
    """A provider that reports its page, then waits for the test or for `stop`."""

    calls: ClassVar[list[tuple[str, object]]] = []
    release = threading.Event()
    outcome = SignInOutcome(state="succeeded", message="Signed in.", account="ada@notes.invalid")
    tool = "ready"
    raises: BaseException | None = None

    def status(self, *, live: bool) -> SignInStatus:
        Fake.calls.append(("status", live))
        return SignInStatus(
            signed_in=True,
            account="ada@notes.invalid",
            grants=[status(READ, "granted"), status(SHARE, "unknown")],
            tool=Fake.tool,
            install="brew install notes",
            live=live,
        )

    def sign_in(self, grants, progress, stop) -> SignInOutcome:
        Fake.calls.append(("sign_in", grants))
        if Fake.raises is not None:
            raise Fake.raises
        progress(SignInProgress(url=PAGE, finish_on="host"))
        while not (Fake.release.is_set() or stop.is_set()):
            stop.wait(0.01)
        if stop.is_set():
            return SignInOutcome(state="cancelled", message="stopped by the caller")
        return Fake.outcome

    def sign_out(self) -> str:
        Fake.calls.append(("sign_out", None))
        return "gone"


@pytest.fixture
def sign_ins(make_kernel):
    Fake.calls = []
    Fake.release = threading.Event()
    Fake.outcome = SignInOutcome(state="succeeded", message="Signed in.", account="ada@notes.invalid")
    Fake.tool = "ready"
    Fake.raises = None
    module = Module("notes", "Reads Notes for tests.")
    module.provides("documents", modes={"read"}, hosts=["notes.invalid"])(Notes)
    module.sign_in(grants=[READ, WRITE, SHARE])(Fake)
    kernel = make_kernel(notes=module)
    found = SignIns(kernel)
    found.clock = lambda: NOW
    OPENED.clear()
    found.opener = OPENED.append
    return found


def wait_for(sign_ins: SignIns, flow: str, state: str) -> None:
    for _ in range(500):
        if sign_ins.flow(flow).state == state:
            return
        threading.Event().wait(0.01)
    pytest.fail(f"never {state}: {sign_ins.flow(flow)}")


def test_the_status_lists_every_declared_grant_and_unknown_stays_unknown(sign_ins):
    access = sign_ins.access("notes", "Notes")
    assert [(g.grant.id, g.state) for g in access.status.grants] == [
        ("notes-read", "granted"),
        ("notes-share", "unknown"),
        ("notes-write", "unknown"),
    ]
    assert (access.status.live, access.flow, Fake.calls) == (False, None, [("status", False)])


def test_the_web_starts_a_sign_in_polls_its_page_and_sees_it_succeed(sign_ins):
    started = sign_ins.connect("notes", frozenset({"notes-write"}), wait=False, prompter=ScriptedPrompter())
    assert (started.state in {"starting", "waiting"}, started.grants) == (True, ["notes-read", "notes-write"])
    assert Fake.calls[0] == ("status", True)
    wait_for(sign_ins, started.id, "waiting")
    assert sign_ins.flow(started.id).progress == SignInProgress(url=PAGE, finish_on="host")
    assert sign_ins.open(started.id).id == started.id
    assert OPENED == [PAGE]
    Fake.release.set()
    wait_for(sign_ins, started.id, "succeeded")
    ended = sign_ins.flow(started.id)
    assert (ended.message, ended.account, ended.ended_at) == ("Signed in.", "ada@notes.invalid", NOW)
    assert sign_ins.access("notes", "Notes").flow == ended


def test_on_the_web_head_connect_neither_waits_nor_opens_the_page_without_a_click(sign_ins):
    started = connect(sign_ins, Prompter(), "notes")  # the web head's prompter: nobody is told
    assert started.state in {"starting", "waiting"}
    wait_for(sign_ins, started.id, "waiting")
    assert OPENED == []
    sign_ins.open(started.id)
    assert OPENED == [PAGE]
    sign_ins.cancel(started.id)


def test_with_nothing_held_needed_or_allowed_a_first_sign_in_asks_for_the_defaults(sign_ins, monkeypatch):
    def nothing(self, *, live: bool) -> SignInStatus:
        return SignInStatus(signed_in=False, grants=[status(READ, "missing"), status(WRITE, "missing")], live=live)

    monkeypatch.setattr(Fake, "status", nothing)
    Fake.release.set()
    ended = sign_ins.connect("notes", frozenset(), wait=True, prompter=ScriptedPrompter())
    assert (ended.state, ended.grants) == ("succeeded", ["notes-read"])


def test_the_cli_waits_is_told_the_page_once_and_it_opens_here(sign_ins):
    prompter = ScriptedPrompter()
    threading.Timer(0.05, Fake.release.set).start()
    ended = sign_ins.connect("notes", frozenset(), wait=True, prompter=prompter)
    assert ended.state == "succeeded"
    assert prompter.transcript == [f"Sign in on this page: {PAGE}"]
    assert OPENED == [PAGE]


def test_cancel_stops_the_tool_and_says_nothing_changed(sign_ins):
    started = sign_ins.connect("notes", frozenset(), wait=False, prompter=ScriptedPrompter())
    wait_for(sign_ins, started.id, "waiting")
    cancelled = sign_ins.cancel(started.id)
    assert (cancelled.state, cancelled.message) == ("cancelled", "The sign-in was cancelled; nothing changed.")
    with pytest.raises(HeinzelError, match="already ended"):
        sign_ins.open(started.id)


def test_a_sign_in_that_gets_no_consent_in_time_times_out(sign_ins):
    sign_ins.timeout = 0.05
    ended = sign_ins.connect("notes", frozenset(), wait=True, prompter=ScriptedPrompter())
    assert (ended.state, ended.message) == ("timed-out", "No consent arrived within 0.000833333 minutes; try again.")


def test_asking_again_stops_the_sign_in_still_running(sign_ins):
    first = sign_ins.connect("notes", frozenset(), wait=False, prompter=ScriptedPrompter())
    wait_for(sign_ins, first.id, "waiting")
    second = sign_ins.connect("notes", frozenset({"notes-write"}), wait=False, prompter=ScriptedPrompter())
    wait_for(sign_ins, first.id, "cancelled")
    assert sign_ins.flow(second.id).state in {"starting", "waiting"}
    sign_ins.cancel(second.id)


@pytest.mark.parametrize(
    ("raised", "message"),
    [
        (HeinzelError("No OAuth client: run notes setup."), "No OAuth client: run notes setup."),
        (RuntimeError("token=ya29.secret"), "The sign-in failed (RuntimeError)."),
    ],
)
def test_a_failing_provider_fails_the_sign_in_without_its_exception_text(sign_ins, raised, message):
    Fake.raises = raised
    ended = sign_ins.connect("notes", frozenset(), wait=True, prompter=ScriptedPrompter())
    assert (ended.state, ended.message) == ("failed", message)


@pytest.mark.parametrize("raised", [SystemExit(1), KeyboardInterrupt()])
def test_a_provider_escaping_with_a_base_exception_still_ends_the_sign_in(sign_ins, raised):
    from heinzel.modules.system.connector import _sign_in

    Fake.raises = raised
    running = _sign_in._Running(SignInFlow(id="f", connector="notes", state="starting", grants=[], started_at=NOW))
    with pytest.raises(type(raised)):
        sign_ins._run(running, Fake(), frozenset(), lambda _: None)
    assert running.done.is_set()
    assert (running.flow.state, running.flow.message) == ("failed", "The sign-in stopped unexpectedly.")


def test_asking_again_waits_for_the_first_tool_to_stop(sign_ins):
    first = sign_ins.connect("notes", frozenset(), wait=False, prompter=ScriptedPrompter())
    wait_for(sign_ins, first.id, "waiting")
    sign_ins.connect("notes", frozenset(), wait=False, prompter=ScriptedPrompter())
    assert sign_ins.flow(first.id).state == "cancelled"  # ended before the second provider started


def test_a_missing_tool_refuses_to_start_and_names_its_supported_install(sign_ins):
    Fake.tool = "missing"
    with pytest.raises(HeinzelError, match=r"its tool is missing\. Install it: brew install notes"):
        sign_ins.connect("notes", frozenset(), wait=False, prompter=ScriptedPrompter())


def test_unknown_names_say_what_exists(sign_ins):
    with pytest.raises(HeinzelError, match="'mail' has no sign-in hz can run; the connectors that have one are: notes"):
        sign_ins.access("mail", "Mail")
    with pytest.raises(HeinzelError, match="has no grant notes-delete; its grants are: notes-read, notes-share"):
        sign_ins.connect("notes", frozenset({"notes-delete"}), wait=False, prompter=ScriptedPrompter())
    with pytest.raises(HeinzelError, match="No sign-in 'nope' is known here"):
        sign_ins.flow("nope")


def test_open_needs_a_page_and_a_browser(sign_ins):
    started = sign_ins.connect("notes", frozenset(), wait=False, prompter=ScriptedPrompter())
    wait_for(sign_ins, started.id, "waiting")

    def no_browser(url: str) -> None:
        raise FileNotFoundError(url)

    sign_ins.opener = no_browser
    with pytest.raises(HeinzelError, match=r"No browser opened on this machine \(FileNotFoundError\)"):
        sign_ins.open(started.id)
    sign_ins.cancel(started.id)


def test_disconnect_signs_out_through_the_provider(sign_ins):
    access = sign_ins.disconnect("notes", "Notes")
    assert (access.message, ("sign_out", None) in Fake.calls) == ("gone", True)


def test_the_report_offers_the_in_app_sign_in_first(make_kernel, sign_ins):
    kernel = sign_ins._kernel
    [connector] = ConnectorService(kernel, kernel.resolve(CapabilityView), sign_ins).report().connectors
    assert (connector.sign_in, connector.connect) == (True, ["hz system connector connect notes"])


class _Interrupted(threading.Event):
    """A done event whose first wait is interrupted, as Ctrl-C does in a terminal."""

    interrupted = False

    def wait(self, timeout: float | None = None) -> bool:
        if not _Interrupted.interrupted:
            _Interrupted.interrupted = True
            raise KeyboardInterrupt
        return super().wait(timeout)


def test_ctrl_c_while_waiting_stops_the_vendors_tool(sign_ins, monkeypatch):
    from heinzel.modules.system.connector import _sign_in

    _Interrupted.interrupted = False
    running = _sign_in._Running
    monkeypatch.setattr(_sign_in, "_Running", lambda flow: running(flow, done=_Interrupted()))
    with pytest.raises(KeyboardInterrupt):
        sign_ins.connect("notes", frozenset(), wait=True, prompter=ScriptedPrompter())
    [flow] = [r.flow for r in sign_ins._flows.values()]
    assert flow.state == "cancelled"
