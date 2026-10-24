"""Each Heinzelmännchen's append-only log, and its copy in the team repository with one writer per file (ADR 0053)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from heinzel import HeinzelError
from heinzel.kernel import OrgContext
from heinzel.modules.telemetry import TeamTelemetry, TelemetryService, TelemetrySettings

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 23, 14, 0, tzinfo=UTC)
ROW = "https://tracker.acme.example/boards/7#costas"


class Team(OrgContext):
    def __init__(self, checkout: Path | None) -> None:
        self.checkout = checkout

    def team_checkout(self) -> Path | None:
        return self.checkout


class Kernel:
    """Just what the service asks the kernel: the team's checkout, through `OrgContext`."""

    def __init__(self, checkout: Path | None, *, team: bool = True) -> None:
        self.team = Team(checkout)
        self.has_team = team

    def provides(self, kind: type) -> bool:
        return self.has_team and kind is OrgContext

    def resolve(self, kind: type) -> OrgContext:
        return self.team


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    path = tmp_path / "team"
    path.mkdir()
    return path


def service(tmp_path: Path, checkout: Path | None, *, user: str | None = "costas", team: bool = True):
    kernel = Kernel(checkout, team=team)
    settings = TelemetrySettings(user=user)
    return TelemetryService(kernel, settings, tmp_path / "state", clock=lambda: NOW)  # type: ignore[arg-type]


def published(**fields: object) -> dict[str, object]:
    return {"heinzel_id": "costas-tracker", "verb": "published", "target": ROW} | fields


def test_report_appends_one_line_per_entry_to_the_heinzels_own_log(tmp_path, checkout):
    telemetry = service(tmp_path, checkout)
    first = telemetry.report(published(), user=None, at=None)
    second = telemetry.report(published(version="r2"), user=None, at=NOW - timedelta(hours=1))
    assert (first.entries, second.entries) == (1, 2)
    lines = second.log.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["time"] for line in lines] == ["2026-10-23T14:00:00Z", "2026-10-23T13:00:00Z"]
    assert "needed_by" not in lines[0]  # unset fields are left out of the line
    assert second.log == tmp_path / "state" / "telemetry" / "costas-tracker.jsonl"
    assert oct(second.log.stat().st_mode & 0o777) == "0o600"
    assert first.next == ["copy your logs into the team repository: hz report sync"]


def test_without_a_team_the_next_step_is_joining_one(tmp_path):
    assert service(tmp_path, None).report(published(), user=None, at=None).next[0].startswith("join your team")
    assert service(tmp_path, None, team=False).checkout is None


def test_without_a_handle_report_says_how_to_set_it(tmp_path, checkout):
    with pytest.raises(HeinzelError, match=r"hz system config set report\.user <your handle>"):
        service(tmp_path, checkout, user=None).report(published(), user=None, at=None)
    assert service(tmp_path, checkout, user=None).report(published(), user="mira", at=None).entry.user == "mira"


@pytest.mark.parametrize(
    ("at", "why"),
    [
        (datetime(2026, 10, 23, 14, 0), "needs an offset"),  # noqa: DTZ001 - a naive time is the point
        (NOW + timedelta(hours=1), "never in the future"),
        (NOW - timedelta(days=31), "within the last 30 days"),
    ],
)
def test_report_dates_entries_only_within_the_last_month(tmp_path, checkout, at, why):
    with pytest.raises(HeinzelError, match=why):
        service(tmp_path, checkout).report(published(), user=None, at=at)


def test_an_invalid_entry_says_every_problem_and_appends_nothing(tmp_path, checkout):
    telemetry = service(tmp_path, checkout)
    with pytest.raises(HeinzelError, match=r"Not reported: target: .*https URL"):
        telemetry.report(published(target="/tmp/row.md"), user=None, at=None)  # noqa: S108 - a local path is the point
    assert not (tmp_path / "state" / "telemetry" / "costas-tracker.jsonl").exists()


def test_a_log_has_one_writer(tmp_path, checkout):
    telemetry = service(tmp_path, checkout)
    telemetry.report(published(), user=None, at=None)
    with pytest.raises(HeinzelError, match="holds the entries of costas, not mira: one writer per log"):
        telemetry.report(published(), user="mira", at=None)


def test_logs_are_never_written_through_a_symbolic_link(tmp_path, checkout):
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "telemetry").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(HeinzelError, match="is a symbolic link"):
        service(tmp_path, checkout).report(published(), user=None, at=None)
    (tmp_path / "state" / "telemetry").unlink()
    (tmp_path / "state" / "telemetry").mkdir()
    (tmp_path / "state" / "telemetry" / "costas-tracker.jsonl").symlink_to(tmp_path / "elsewhere" / "x")
    with pytest.raises(HeinzelError, match="Can't write"):
        service(tmp_path, checkout).report(published(), user=None, at=None)


def test_a_log_that_grew_too_large_is_refused(tmp_path, checkout, monkeypatch):
    monkeypatch.setattr("heinzel.modules.telemetry._service.MAX_LOG", 10)
    with pytest.raises(HeinzelError, match="a log holds metadata"):
        service(tmp_path, checkout).report(published(), user=None, at=None)


def test_sync_copies_each_log_under_its_user_and_counts_whats_new(tmp_path, checkout):
    telemetry = service(tmp_path, checkout)
    telemetry.report(published(), user=None, at=None)
    synced = telemetry.sync()
    (log,) = synced.logs
    assert (log.path, log.entries, log.new) == (checkout / "telemetry" / "costas" / "costas-tracker.jsonl", 1, 1)
    assert synced.next[0].startswith("commit and push them")
    telemetry.report(published(version="r2"), user=None, at=None)
    (log,) = telemetry.sync().logs
    assert (log.entries, log.new) == (2, 1)
    assert telemetry.sync().logs[0].new == 0


def test_sync_refuses_a_copy_its_log_doesnt_extend_and_links(tmp_path, checkout):
    telemetry = service(tmp_path, checkout)
    telemetry.report(published(), user=None, at=None)
    copy = checkout / "telemetry" / "costas" / "costas-tracker.jsonl"
    copy.parent.mkdir(parents=True)
    copy.write_text('{"someone": "else"}\n', encoding="utf-8")
    synced = telemetry.sync()
    assert synced.logs == []
    assert "each log has one writer" in synced.problems[0]
    copy.unlink()
    copy.symlink_to(tmp_path / "anywhere")
    assert "is a symbolic link" in telemetry.sync().problems[0]


def test_sync_reports_a_broken_local_log_and_needs_a_team(tmp_path, checkout):
    telemetry = service(tmp_path, checkout)
    telemetry.report(published(), user=None, at=None)
    (tmp_path / "state" / "telemetry" / "broken.jsonl").write_text("{not json\n", encoding="utf-8")
    (tmp_path / "state" / "telemetry" / "empty.jsonl").write_text("", encoding="utf-8")
    synced = telemetry.sync()
    assert [log.heinzel_id for log in synced.logs] == ["costas-tracker"]
    assert synced.problems[0].startswith("broken.jsonl: ")
    assert synced.problems[1] == "empty.jsonl: empty"
    with pytest.raises(HeinzelError, match="hz team join <url>"):
        service(tmp_path, None).sync()


def test_sync_says_when_the_checkout_cant_be_written(tmp_path, checkout):
    telemetry = service(tmp_path, checkout)
    telemetry.report(published(), user=None, at=None)
    (checkout / "telemetry").write_text("a file, not a directory", encoding="utf-8")
    assert "can't copy it" in telemetry.sync().problems[0]


def write_team_log(checkout: Path, user: str, name: str, *lines: str) -> Path:
    path = checkout / "telemetry" / user / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


def line(**fields: object) -> str:
    entry = {"heinzel_id": "priya-tracker", "user": "priya", "time": "2026-10-21T11:05:00Z", "verb": "heartbeat"}
    return json.dumps(entry | fields)


def test_reading_validates_the_teams_copies_again_and_names_what_it_skips(tmp_path, checkout):
    write_team_log(
        checkout,
        "priya",
        "priya-tracker",
        line(),
        "",
        "{broken",
        line(user="mira"),
        line(heinzel_id="other"),
        "x" * 5000,
    )
    write_team_log(checkout, "Not-A-Handle", "x", line())
    write_team_log(checkout, "priya", "Bad_Name", line())
    (checkout / "telemetry" / "README.md").write_text("not a folder", encoding="utf-8")
    read = TeamTelemetry(service(tmp_path, checkout)).entries()
    assert [(e.user, e.heinzel_id) for e in read.entries] == [("priya", "priya-tracker")]
    problems = "\n".join(read.problems)
    assert "telemetry/Not-A-Handle: not a roster handle, skipped" in problems
    assert "telemetry/priya/Bad_Name.jsonl: not a Heinzelmännchen's log, skipped" in problems
    assert "telemetry/priya/priya-tracker.jsonl:3:" in problems
    assert "an entry of mira/priya-tracker filed here, skipped" in problems
    assert "an entry of priya/other filed here, skipped" in problems
    assert "longer than 4096 bytes" in problems


def test_your_own_log_wins_over_its_older_copy_and_entries_come_oldest_first(tmp_path, checkout):
    telemetry = service(tmp_path, checkout)
    write_team_log(checkout, "costas", "costas-tracker", line(heinzel_id="costas-tracker", user="costas"))
    write_team_log(checkout, "priya", "priya-tracker", line(time="2026-10-20T08:00:00Z"))
    telemetry.report(published(), user=None, at=None)
    read = telemetry.read()
    assert [(e.heinzel_id, e.verb) for e in read.entries] == [
        ("priya-tracker", "heartbeat"),
        ("costas-tracker", "published"),
    ]


def test_a_copy_too_large_or_unreadable_is_skipped(tmp_path, checkout, monkeypatch):
    path = write_team_log(checkout, "priya", "priya-tracker", line())
    monkeypatch.setattr("heinzel.modules.telemetry._service.MAX_LOG", 10)
    assert "over 0 MiB, skipped" in service(tmp_path, checkout).read().problems[0]
    monkeypatch.undo()
    path.chmod(0)
    try:
        assert "can't be read" in service(tmp_path, checkout).read().problems[0]
    finally:
        path.chmod(0o600)
