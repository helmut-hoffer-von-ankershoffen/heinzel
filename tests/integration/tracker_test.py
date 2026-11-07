"""The tracker capability's write contract through the heads (ADR 0136): an issue's life round-trips through an adapter.

The adapter is test-only and in memory, as the acme fixture tracker: core ships no tracker adapter (ADR 0094).
"""

import inspect
import json
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from typer.testing import CliRunner

from heinzel import Attachment, Config, HeinzelError, Issue, Kernel, Module, Tracker, Window
from heinzel.heads import api, mcp
from heinzel.heads.cli import build
from heinzel.kernel import CAPABILITIES

pytestmark = pytest.mark.integration

START = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)


class AcmeTracker:
    """A tracker in memory that implements every method of `Tracker`, recording each write with its actor."""

    issues: ClassVar[dict[str, Issue]] = {}
    events: ClassVar[list[tuple[str, str, str]]] = []
    files: ClassVar[dict[str, bytes]] = {}
    clock: ClassVar[list[datetime]] = [START]

    @classmethod
    def reset(cls) -> None:
        cls.issues, cls.events, cls.files, cls.clock = {}, [], {}, [START]

    def _now(self) -> datetime:
        self.clock[0] += timedelta(minutes=1)
        return self.clock[0]

    def _issue(self, key: str) -> Issue:
        if key not in self.issues:
            msg = f"No issue {key} in the acme tracker"
            raise HeinzelError(msg)
        return self.issues[key]

    def _write(self, key: str, actor: str, event: str, **changes: object) -> Issue:
        issue = self._issue(key).model_copy(update={"updated_at": self._now(), **changes})
        self.issues[key] = issue
        self.events.append((key, actor, event))
        return issue

    def issues_updated(self, project: str, window: Window) -> list[Issue]:
        return sorted(
            (
                i
                for i in self.issues.values()
                if i.key.startswith(f"{project}-") and window.since <= i.updated_at < window.until
            ),
            key=lambda i: i.updated_at,
        )

    def create(self, project: str, title: str, body: str, reporter: str, labels: list[str]) -> Issue:
        key = f"{project}-{len(self.issues) + 1}"
        now = self._now()
        self.issues[key] = Issue(
            key=key,
            title=title,
            url=f"https://acme.invalid/{key}",
            state="open",
            updated_at=now,
            created_at=now,
            reporter=reporter,
            labels=labels,
        )
        self.events.append((key, reporter, f"create: {body}"))
        return self.issues[key]

    def claim(self, key: str, actor: str) -> Issue:
        holder = self._issue(key).assignee
        if holder not in {None, actor}:
            msg = f"{key} is claimed by {holder}"
            raise HeinzelError(msg)
        return self._write(key, actor, "claim", assignee=actor, state="in_progress")

    def transition(self, key: str, state: str, actor: str, note: str | None = None) -> Issue:
        return self._write(key, actor, f"transition: {state}" + (f" ({note})" if note else ""), state=state)

    def note(self, key: str, text: str, actor: str) -> Issue:
        return self._write(key, actor, f"note: {text}")

    def attach(self, key: str, name: str, data: bytes, media_type: str, actor: str) -> Attachment:
        ref = f"issues/{key}/{name}"
        self.files[ref] = data
        attachment = Attachment(name=name, media_type=media_type, size=len(data), ref=ref)
        self._write(key, actor, f"attach: {name}", attachments=[*self._issue(key).attachments, attachment])
        return attachment

    def close(self, key: str, actor: str, reason: str) -> Issue:
        now = self._now()
        return self._write(key, actor, f"close: {reason}", state="closed", closed_at=now)


def acme() -> Module:
    module = Module("acme", "Test-only tracker adapter.")
    module.provides("tracker", modes={"read", "write"}, hosts=["acme.invalid"])(AcmeTracker)
    return module


def board() -> Module:
    module = Module("board", "Reports, works on and closes issues.", requires=["tracker:read"])

    @module.operation
    def recent(tracker: Tracker) -> list[str]:
        """List the issues of ACME that changed today, with their state and who has them."""
        window = Window(since=START, until=START + timedelta(days=1))
        return [f"{i.key} {i.state} {i.assignee or '-'}" for i in tracker.issues_updated("ACME", window)]

    @module.operation
    def sneak(tracker: Tracker) -> str:
        """Try to report an issue with a read-only declaration."""
        return tracker.create("ACME", "sneaky", "", "nobody", []).key

    @module.operation(read_only=False, requires=["tracker:write"])
    def report(tracker: Tracker, title: str) -> str:
        """Report an issue for Mira."""
        return tracker.create("ACME", title, "From the report button.", "mira", ["owner-ask"]).key

    @module.operation(read_only=False, requires=["tracker:write"])
    def take(tracker: Tracker, key: str, actor: str) -> str:
        """Claim an issue."""
        return tracker.claim(key, actor).state

    @module.operation(read_only=False, requires=["tracker:write"])
    def work(tracker: Tracker, key: str, actor: str) -> str:
        """Comment on, attach a screenshot to and move an issue to review."""
        tracker.note(key, "Found the cause.", actor)
        attachment = tracker.attach(key, "screen.png", b"\x89PNG", "image/png", actor)
        return f"{tracker.transition(key, 'review', actor, note='Fix ready.').state} {attachment.size}"

    @module.operation(read_only=False, requires=["tracker:write"])
    def finish(tracker: Tracker, key: str, actor: str) -> str:
        """Close an issue as done."""
        return tracker.close(key, actor, "Fixed.").state

    return module


@pytest.fixture
def kernel(make_kernel) -> Kernel:
    AcmeTracker.reset()
    config = Config(files=(), data={"capabilities": {"tracker": "acme"}})
    return make_kernel(builtins=True, acme=acme(), board=board(), config=config)


def hz(kernel: Kernel, *args: str) -> tuple[int, object]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, result.output


def test_the_acme_tracker_implements_the_whole_contract():
    tracker: Tracker = AcmeTracker()  # the type checker holds it to the protocol
    for method in CAPABILITIES["tracker"].methods:
        assert inspect.signature(getattr(tracker, method)).parameters.keys() == (
            inspect.signature(getattr(Tracker, method)).parameters.keys() - {"self"}
        ), method


def test_an_issue_round_trips_create_claim_transition_and_close_with_every_actor_recorded(kernel):
    code, key = hz(kernel, "board", "report", "Crash on save")
    assert (code, key) == (0, "ACME-1")
    assert hz(kernel, "board", "take", "ACME-1", "builder") == (0, "in_progress")
    assert hz(kernel, "board", "recent") == (0, ["ACME-1 in_progress builder"])
    assert hz(kernel, "board", "work", "ACME-1", "builder") == (0, "review 4")
    assert hz(kernel, "board", "finish", "ACME-1", "builder") == (0, "closed")

    issue = AcmeTracker.issues["ACME-1"]
    assert (issue.reporter, issue.labels, issue.assignee) == ("mira", ["owner-ask"], "builder")
    assert issue.created_at is not None
    assert issue.closed_at is not None
    assert issue.closed_at > issue.created_at
    assert issue.attachments == [
        Attachment(name="screen.png", media_type="image/png", size=4, ref="issues/ACME-1/screen.png")
    ]
    assert AcmeTracker.events == [
        ("ACME-1", "mira", "create: From the report button."),
        ("ACME-1", "builder", "claim"),
        ("ACME-1", "builder", "note: Found the cause."),
        ("ACME-1", "builder", "attach: screen.png"),
        ("ACME-1", "builder", "transition: review (Fix ready.)"),
        ("ACME-1", "builder", "close: Fixed."),
    ]


def test_a_claim_another_actor_holds_is_refused_not_overwritten(kernel):
    hz(kernel, "board", "report", "Crash on save")
    assert hz(kernel, "board", "take", "ACME-1", "builder")[0] == 0
    code, output = hz(kernel, "board", "take", "ACME-1", "reviewer")
    assert code != 0
    assert "ACME-1 is claimed by builder" in str(output)
    assert AcmeTracker.issues["ACME-1"].assignee == "builder"


def test_a_read_only_operation_never_reaches_a_tracker_write_in_any_head(kernel):
    code, output = hz(kernel, "board", "sneak")
    assert code != 0
    assert "board sneak can't call tracker.create, which needs write" in str(output)
    response = TestClient(api.build(kernel), base_url="http://localhost").get("/board/sneak")
    assert response.status_code >= 400
    assert "needs write" in response.text

    async def scenario() -> tuple[bool, str]:
        async with Client(mcp.build(kernel)) as client:
            result = await client.call_tool("board_sneak", {})
            return result.is_error, repr(result.content)

    is_error, content = anyio.run(scenario)
    assert is_error
    assert "needs write" in content
    assert AcmeTracker.issues == {}


def test_capability_list_shows_the_tracker_can_read_and_write_and_who_writes(kernel):
    code, report = hz(kernel, "system", "capability", "list")
    assert code == 0
    tracker = next(c for c in report["capabilities"] if c["name"] == "tracker")
    assert (tracker["modes"], tracker["bound"]) == (["read", "write"], "acme")
    assert [a for a in report["adapters"] if a["capability"] == "tracker"] == [
        {
            "adapter": "acme",
            "capability": "tracker",
            "modes": ["read", "write"],
            "hosts": ["acme.invalid"],
            "module": "acme",
            "distribution": "heinzel-test",
        }
    ]
    assert sorted((u["requester"], u["requirement"]) for u in report["uses"] if u["requester"].startswith("board")) == [
        ("board", "tracker:read"),
        ("board finish", "tracker:write"),
        ("board report", "tracker:write"),
        ("board take", "tracker:write"),
        ("board work", "tracker:write"),
    ]
