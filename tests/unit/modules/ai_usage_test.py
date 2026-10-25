"""The usage ledger and its reports (ADR 0057): counts per run, today and this week, against the daily budget."""

from datetime import UTC, datetime, timedelta

import pytest

from heinzel import AgentResult, Config, Settings, TokenUsage, UsageEntry
from heinzel.modules.ai._usage import AiSettings, UsageLedger, compact, midnight, report

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)


def entry(hours_ago: float, **fields: object) -> UsageEntry:
    base = {"at": NOW - timedelta(hours=hours_ago), "cause": "chat", "access": "claude-code", "input_tokens": 100}
    return UsageEntry.model_validate(base | fields)


@pytest.fixture
def ledger(settings: Settings) -> UsageLedger:
    return UsageLedger(settings, Config(files=(), data={"capabilities": {"ai_access": "gateway"}}))


def test_an_agent_result_becomes_an_entry_without_any_text():
    result = AgentResult(
        runner="claude-code",
        model="sonnet",
        status="completed",
        draft="secret draft",
        duration_seconds=1,
        usage=TokenUsage(input_tokens=10, output_tokens=5, cache_read_tokens=100, cost_usd=0.01),
    )
    found = UsageEntry.of(result, "scheduled", "team/release-notes")
    assert (found.cause, found.heinzel, found.model, found.tokens, found.cost_usd) == (
        "scheduled",
        "team/release-notes",
        "sonnet",
        115,
        0.01,
    )
    assert "secret" not in found.model_dump_json()


def test_entries_are_appended_private_and_unreadable_lines_skipped(ledger):
    ledger.record(entry(1))
    ledger.record(entry(2, access=None))
    with ledger.path.open("a", encoding="utf-8") as file:
        file.write("not json\n")
    found = ledger.entries()
    assert len(found) == 2
    assert found[1].access == "gateway"  # the AI access in use, from the kernel's configuration
    assert ledger.path.stat().st_mode & 0o077 == 0


def test_recording_never_raises(settings, tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("", encoding="utf-8")
    broken = UsageLedger(settings.model_copy(update={"state_dir": blocked}), Config.empty())
    broken.record(entry(1))  # the directory can't be made: logged, not raised


def test_the_report_splits_today_from_the_week_and_breaks_down_by_each_dimension(ledger):
    ledger.record(entry(1, model="sonnet", cost_usd=1.0, heinzel="release-notes", cause="scheduled"))
    ledger.record(entry(2, model="haiku", cost_usd=None, output_tokens=50))
    ledger.record(entry(24 * 3, model="sonnet", cost_usd=2.0))
    ledger.record(entry(24 * 9, model="sonnet", cost_usd=9.0))  # older than a week
    found = report(ledger, AiSettings(daily_budget_usd=4.0), "today", "model", now=NOW)
    assert (found.today.runs, found.today.cost_usd, found.today.unpriced) == (2, 1.0, 1)
    assert (found.week.runs, found.week.cost_usd) == (3, 3.0)
    assert [(r.key, r.tokens) for r in found.breakdowns["model"]] == [("haiku", 150), ("sonnet", 100)]
    assert [r.key for r in found.breakdowns["heinzel"]] == ["unknown", "release-notes"]
    assert {r.key for r in found.breakdowns["cause"]} == {"scheduled", "chat"}
    assert (found.level, found.tone, found.label) == (0.25, "ok", "250 tok")
    week = report(ledger, AiSettings(), "week", "access", now=NOW)
    assert week.breakdowns["access"][0].runs == 3


@pytest.mark.parametrize(("spent", "tone", "level"), [(3.0, "warn", 0.75), (4.0, "over", 1.0), (8.0, "over", 1.0)])
def test_the_meter_turns_amber_then_red(ledger, spent, tone, level):
    ledger.record(entry(0.5, cost_usd=spent))
    found = report(ledger, AiSettings(daily_budget_usd=4.0), "today", "model", now=NOW)
    assert (found.tone, found.level) == (tone, level)


@pytest.mark.parametrize(
    ("tokens", "label"), [(950, "950 tok"), (1234, "1.2k tok"), (182_000, "182k tok"), (2_500_000, "2.5M tok")]
)
def test_compact_labels(tokens, label):
    assert compact(tokens) == label


@pytest.mark.parametrize(
    "reported",
    [
        TokenUsage(input_tokens=-5, cost_usd=float("nan")),
        TokenUsage(output_tokens=-1, cost_usd=-1.0),
        TokenUsage(cache_read_tokens=-2, cost_usd=float("inf")),
    ],
)
def test_nonsense_a_runner_reports_never_fails_the_entry(reported):
    result = AgentResult(runner="x", status="completed", duration_seconds=1, usage=reported)
    found = UsageEntry.of(result, "chat")
    assert (found.tokens, found.cost_usd) == (0, None)


def test_a_big_ledger_is_pruned_to_recent_lines(ledger, monkeypatch):
    import heinzel.modules.ai._usage as usage

    monkeypatch.setattr(usage, "LIMIT", 2000)
    ledger.record(entry(24 * 90))  # older than KEEP: dropped
    for hours in range(30):
        ledger.record(entry(hours / 10))
    assert ledger.path.stat().st_size <= 2000
    kept = ledger.entries()
    assert kept
    assert all(e.at > NOW - timedelta(days=60) for e in kept)
    assert ledger.path.stat().st_mode & 0o077 == 0


def test_nothing_is_recorded_when_no_module_keeps_a_log(make_kernel):
    from heinzel.kernel import record_usage

    kernel = make_kernel()
    record_usage(kernel, entry(1))  # no ai module: nothing provides UsageLog, nothing happens


def test_midnight_is_local():
    assert midnight(NOW) <= NOW
    assert NOW - midnight(NOW) < timedelta(days=1)
