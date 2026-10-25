"""The adapter bound through configuration, reading through a bound test-only mail adapter, through the CLI. Offline."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from heinzel import AgentResult, Config, Kernel, Meeting, Meetings, Module, Transcript, Window
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

BOUND = Config(files=(), data={"capabilities": {"mail": "inbox", "meetings": "zoom"}})
SINCE, UNTIL = "2026-10-01T00:00:00+00:00", "2026-11-01T00:00:00+00:00"


def prep() -> Module:
    """A test-only requester, as a workflow plugin would declare it: a capability, never a vendor."""
    module = Module("prep", "Test-only meeting preparation.", requires=["meetings:read"])

    @module.operation
    def ended(meetings: Meetings, since: str, until: str) -> list[Meeting]:
        """List ended meetings."""
        return meetings.ended(Window(since=datetime.fromisoformat(since), until=datetime.fromisoformat(until)))

    @module.operation
    def said(meetings: Meetings, since: str, until: str) -> list[Transcript]:
        """List transcripts."""
        return meetings.transcripts(Window(since=datetime.fromisoformat(since), until=datetime.fromisoformat(until)))

    @module.operation
    def one(meetings: Meetings, meeting: str) -> Transcript:
        """Read one transcript."""
        return meetings.transcript(meeting)

    return module


def hz(kernel: Kernel, *args: str) -> tuple[int, object]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, result.output


@pytest.fixture
def kernel(zoom_kernel, mailbox, emails) -> Kernel:
    mailbox.holding(emails("sync"), emails("news", subject="Weekly news"))
    return zoom_kernel(BOUND, prep=prep())


def test_a_requester_lists_meetings_from_summary_emails_through_the_bound_mail_adapter(kernel, mailbox):
    code, meetings = hz(kernel, "prep", "ended", SINCE, UNTIL)
    assert code == 0, meetings
    assert meetings == [
        {
            "id": "sync",
            "title": "Weekly sync",
            "started_at": "2026-10-20T09:01:00Z",
            "ended_at": "2026-10-20T09:31:00Z",
            "participants": [{"name": "Mira Holm", "email": None}, {"name": "Jonas Berg", "email": None}],
            "conference_id": "81234567890",
            "url": "https://us06web.zoom.us/meeting/summary/abc123",
            "has_transcript": True,
        }
    ]
    assert mailbox.queries == ["from:zoom.us"]


def test_a_requester_reads_the_summaries_as_transcripts_and_one_by_id(kernel):
    code, transcripts = hz(kernel, "prep", "said", SINCE, UNTIL)
    assert code == 0, transcripts
    assert transcripts[0]["segments"][1] == {
        "speaker": None,
        "offset_seconds": 0.0,
        "text": "Next steps\n- Jonas Berg will update the release notes.\n"
        "- Priya: check the crash reports before Thursday.",
    }
    code, one = hz(kernel, "prep", "one", "sync")
    assert code == 0, one
    assert one == transcripts[0]


def test_without_a_mail_adapter_the_requester_fails_and_doctor_says_what_to_bind(zoom_kernel):
    kernel = zoom_kernel(Config(files=(), data={"capabilities": {"meetings": "zoom"}}), prep=prep())
    code, output = hz(kernel, "prep", "ended", SINCE, UNTIL)
    assert code != 0
    assert "mail:read" in str(output)
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    capability = {c["name"]: c for c in report["checks"]["system.capability"]}
    assert capability["mail:read for zoom"]["status"] == "degraded"
    zoom = {c["name"]: c for c in report["checks"]["zoom"]}
    assert zoom["mail"]["status"] == "degraded"
    assert "AI Companion" in zoom["summaries"]["detail"]


def test_alone_it_serves_meetings_automatically(zoom_kernel, mailbox, emails):
    mailbox.holding(emails("sync"))
    code, meetings = hz(
        zoom_kernel(Config(files=(), data={"capabilities": {"mail": "inbox"}}), prep=prep()),
        "prep",
        "ended",
        SINCE,
        UNTIL,
    )
    assert code == 0, meetings
    assert [m["id"] for m in meetings] == ["sync"]


def test_unbound_the_requester_fails_and_doctor_names_the_binding(zoom_kernel, mailbox):
    off = Config(files=(), data={"capabilities": {"meetings": "none"}})
    kernel = zoom_kernel(off, prep=prep())
    code, output = hz(kernel, "prep", "ended", SINCE, UNTIL)
    assert code != 0
    assert "hz system config set capabilities.meetings zoom" in str(output)
    assert mailbox.queries == []
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    zoom = {c["name"]: c for c in report["checks"]["zoom"]}
    assert zoom["bindings"]["status"] == "degraded"


def test_doctor_reads_no_mail(kernel, mailbox):
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    zoom = {c["name"]: c["status"] for c in report["checks"]["zoom"]}
    assert zoom == {"summaries": "up", "mail": "up", "meetings": "up"}
    assert (mailbox.queries, mailbox.reads) == ([], [])


def test_capability_list_shows_the_adapter_reading_through_mail(kernel):
    code, report = hz(kernel, "system", "capability", "list")
    assert code == 0
    assert {c["name"]: c["bound"] for c in report["capabilities"]}["meetings"] == "zoom"
    uses = {(u["requester"], u["requirement"], u["adapter"]) for u in report["uses"]}
    assert ("zoom", "mail:read", "inbox") in uses
    assert ("prep", "meetings:read", "zoom") in uses


def test_a_project_file_cant_choose_which_mail_is_read(zoom_kernel, mailbox, emails, tmp_path, monkeypatch):
    user = tmp_path / "user"
    user.mkdir()
    (user / "config.toml").write_text('[capabilities]\nmail = "inbox"\nmeetings = "zoom"\n', encoding="utf-8")
    monkeypatch.setenv("HZ_CONFIG_HOME", str(user))
    project = tmp_path / "project"
    project.mkdir()
    (project / "hz.toml").write_text('[zoom]\nquery = "label:Private"\n', encoding="utf-8")
    mailbox.holding(emails("sync"))
    kernel = zoom_kernel(Config.load(project), prep=prep())
    assert hz(kernel, "prep", "ended", SINCE, UNTIL)[0] == 0
    assert mailbox.queries == ["from:zoom.us"]
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    assert "project setting zoom.query" in [c["name"] for c in report["checks"]["system.doctor"]]


# A workflow contract reads the meetings; the packet an agent would get names people but holds no address.

CONTRACT = """---
name: meeting-notes
kind: responsibility
version: 1.0.0
description: Notes of this week's meetings.
hz:
  schema: 1
  title: Meeting notes
  requires: [meetings:read]
  inputs:
    said:
      read: meetings.transcripts
      window: 7d
  outputs:
    notes: {format: markdown}
  agent:
    tools: [Read]
    max_usd: 0.5
  timeout: 10m
---

# Meeting notes

### Goal

Notes of what was decided.

### Requires

- `said`: summaries of meetings that ended in the last seven days.

### Maintains

- `notes`: what was decided, by meeting.

### Done when

- [ ] Every decision names its meeting.
"""


class Runner:
    """A fake agent runner; never called by a dry run."""

    def run(self, prompt, *, contract, tools, timeout, budget) -> AgentResult:  # pragma: no cover - dry runs only
        return AgentResult(runner="fake", status="completed", draft=prompt, duration_seconds=0.1)


def test_a_contract_reads_meetings_and_its_packet_has_names_but_no_addresses(zoom_kernel, mailbox, emails, tmp_path):
    mailbox.holding(emails("sync", received=datetime.now(UTC) - timedelta(days=1)))
    contracts = tmp_path / "workflows"
    contracts.mkdir()
    (contracts / "meeting-notes.prose.md").write_text(CONTRACT, encoding="utf-8")
    runner = Module("fakes", "Test-only agent runner.")
    runner.provides("agent-runner", modes={"read"}, name="fake")(Runner)
    capabilities = {"mail": "inbox", "meetings": "zoom", "agent_runner": "fake"}
    config = Config(files=(), data={"capabilities": capabilities, "heinzel": {"user_dir": str(contracts)}})
    kernel = zoom_kernel(config, fakes=runner)
    code, result = hz(kernel, "heinzel", "wake", "meeting-notes", "--dry-run")
    assert code == 0, result
    packet = json.dumps(result["packet"])
    assert "Jonas Berg" in packet
    assert "release notes" in packet
    assert "@example.com" not in packet
