"""The quarantine lane takes a known flake out of the required jobs only briefly, visibly and with a bead."""

import datetime as dt
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tests import _quarantine

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("quarantine", ROOT / "scripts" / "quarantine.py")
assert spec
assert spec.loader
quarantine = importlib.util.module_from_spec(spec)
sys.modules["quarantine"] = quarantine
spec.loader.exec_module(quarantine)

pytestmark = pytest.mark.unit

DAY = dt.date(2026, 11, 7)
TEST = "tests/unit/x_test.py::test_flaky"


def entry_text(**fields: object) -> str:
    values = {
        "test": f'"{TEST}"',
        "bead": '"3qj"',
        "added": "2026-11-07",
        "until": "2026-11-10",
        "reason": '"times out on a loaded runner"',
    }
    values.update({key: value for key, value in fields.items() if value is not None})
    body = "\n".join(f"{key} = {value}" for key, value in values.items() if key not in fields or fields[key] != "")
    return f"[[quarantine]]\n{body}\n"


def test_a_complete_entry_is_read():
    (entry,) = quarantine.parse(entry_text())
    assert (entry.test, entry.bead, entry.added, entry.until) == (TEST, "3qj", DAY, dt.date(2026, 11, 10))
    assert quarantine.problems([entry], DAY) == []


@pytest.mark.parametrize(
    ("fields", "says"),
    [
        ({"bead": ""}, "needs exactly"),
        ({"until": ""}, "needs exactly"),
        ({"bead": '"Not a bead!"'}, "`bead` must be a bead id"),
        ({"until": "2026-11-15"}, "0-7 days after"),
        ({"until": "2026-11-06"}, "0-7 days after"),
        ({"until": '"2026-11-10"'}, "must be a date"),
        ({"added": "2026-11-07T10:00:00"}, "must be a date"),
        ({"test": '"tests/unit/x_test.py"'}, "one node id"),
        ({"test": '"tests/"'}, "one node id"),
        ({"test": '"scripts/x.py::test_y"'}, "one node id"),
        ({"reason": '""'}, "one line of 1-200"),
        ({"reason": f'"{"x" * 201}"'}, "one line of 1-200"),
        ({"owner": '"someone"'}, "unknown ['owner']"),
    ],
)
def test_an_entry_without_a_bead_and_a_near_deadline_is_refused(fields, says):
    text = entry_text(**fields)
    with pytest.raises(quarantine.RegistryError, match=re.escape(says)):
        quarantine.parse(text)


def test_at_most_eight_entries():
    rows = [entry_text(test=f'"tests/unit/x_test.py::test_{n}"') for n in range(quarantine.MAX_ENTRIES + 1)]
    nine, eight = "".join(rows), "".join(rows[:-1])
    with pytest.raises(quarantine.RegistryError, match="9 entries, at most 8"):
        quarantine.parse(nine)
    assert len(quarantine.parse(eight)) == quarantine.MAX_ENTRIES


@pytest.mark.parametrize(
    ("text", "says"),
    [
        (entry_text() + entry_text(test=f'"{TEST}[phone]"'), "overlaps"),
        ("max_entries = 99\n" + entry_text(), "top-level"),
        ("[[quarantine]\n", "not valid TOML"),
    ],
)
def test_overlapping_entries_and_stray_keys_are_refused(text, says):
    with pytest.raises(quarantine.RegistryError, match=says):
        quarantine.parse(text)


def test_an_entry_past_until_quarantines_nothing_and_fails_the_check():
    (entry,) = quarantine.parse(entry_text())
    late = dt.date(2026, 11, 11)
    assert quarantine.quarantined(TEST, [entry], dt.date(2026, 11, 10)) == entry
    assert quarantine.quarantined(TEST, [entry], late) is None
    assert "past until 2026-11-10 (bead 3qj)" in quarantine.problems([entry], late)[0]
    assert "in the future" in quarantine.problems([entry], dt.date(2026, 11, 6))[0]
    assert "OVERDUE by 1 day(s)" in quarantine.describe(entry, late)


def test_an_entry_matches_its_parametrized_cases_but_not_a_similar_name():
    (entry,) = quarantine.parse(entry_text())
    assert entry.matches(f"{TEST}[phone]")
    assert not entry.matches(f"{TEST}_two")
    (cls,) = quarantine.parse(entry_text(test='"tests/unit/x_test.py::TestGroup"'))
    assert not cls.matches("tests/unit/x_test.py::TestGroup::test_one")  # one entry never empties a whole class


def test_the_check_command_fails_on_an_overdue_entry_and_an_invalid_registry(tmp_path, capsys):
    registry = tmp_path / "quarantine.toml"
    registry.write_text(entry_text())
    assert quarantine.main(["check"], on=DAY, registry=registry) == 0
    assert "1/8 entries, all in date" in capsys.readouterr().out
    assert quarantine.main(["check"], on=dt.date(2026, 11, 11), registry=registry) == 1
    registry.write_text(entry_text(bead=""))
    assert quarantine.main(["check"], on=DAY, registry=registry) == 1
    assert quarantine.main(["run"], on=DAY, registry=registry) == 2
    assert quarantine.main(["check"], on=DAY, registry=tmp_path / "absent.toml") == 0


def test_the_plan_selects_the_lane_and_browsers_only_for_entries_in_date(tmp_path, capsys):
    registry = tmp_path / "quarantine.toml"
    registry.write_text(entry_text(test='"tests/e2e/web_ui_test.py::test_phone"'))
    assert quarantine.main(["plan"], on=DAY, registry=registry) == 0
    assert capsys.readouterr().out.split() == ["selected=true", "browsers=true"]
    quarantine.main(["plan"], on=dt.date(2026, 11, 11), registry=registry)
    assert capsys.readouterr().out.split() == ["selected=false", "browsers=false"]


def _runner(script: dict[str, list[dict[str, str]]], calls: list[list[str]]):
    """Each call writes the next scripted outcomes to the plugin's report file."""

    def run(args: list[str], env: dict[str, str]) -> int:
        assert env["HZ_QUARANTINE"] == "only"
        calls.append(args)
        outcomes = script[str(len(calls))]
        with Path(env["HZ_QUARANTINE_REPORT"]).open("a") as handle:
            handle.writelines(
                json.dumps({"nodeid": nodeid, "when": "call", "outcome": outcome}) + "\n"
                for nodeid, outcome in outcomes.items()
            )
        return 1 if "failed" in outcomes.values() else 0

    return run


def test_the_lane_retries_only_failures_by_node_id_and_reports_each_result(tmp_path):
    other = "tests/integration/y_test.py::test_hint"
    gone = "tests/unit/z_test.py::test_gone"
    entries = quarantine.parse(
        entry_text() + entry_text(test=f'"{other}"', bead='"0bb"') + entry_text(test=f'"{gone}"', bead='"zzz"')
    )
    calls: list[list[str]] = []
    runner = _runner(
        {
            "1": {f"{TEST}[a]": "failed", f"{TEST}[b]": "passed", other: "failed"},
            "2": {f"{TEST}[a]": "passed", other: "failed"},
            "3": {other: "failed"},
        },
        calls,
    )
    report = quarantine.run(entries, DAY, attempts=5, runner=runner)
    assert calls == [
        ["tests/integration/y_test.py", "tests/unit/x_test.py", "tests/unit/z_test.py"],
        [other, f"{TEST}[a]"],
        [other],
    ]  # at most 3 attempts, whatever is asked
    status = {row["test"]: row["status"] for row in report["entries"]}
    assert status == {TEST: "flaky", other: "fail", gone: "not-collected"}
    assert report["entries"][0]["nodes"][f"{TEST}[a]"] == ["failed", "passed"]
    text = quarantine.summary(report)
    assert "Non-blocking" in text
    assert f"| `{other}` | 0bb | 2026-11-10 | fail | test_hint: failed failed failed |" in text


def test_the_lane_runs_nothing_for_an_overdue_entry_and_says_so():
    entries = quarantine.parse(entry_text())
    calls: list[list[str]] = []
    report = quarantine.run(entries, dt.date(2026, 11, 11), runner=_runner({}, calls))
    assert calls == []
    assert report["entries"][0]["status"] == "overdue"
    assert "No entries." in quarantine.summary({**report, "entries": []})


def test_a_broken_session_counts_as_a_failure_of_every_named_node():
    entries = quarantine.parse(entry_text())

    def broken(args: list[str], env: dict[str, str]) -> int:
        return 0 if "::" not in args[0] else 3

    calls: list[list[str]] = []

    def first(args: list[str], env: dict[str, str]) -> int:
        calls.append(args)
        if len(calls) == 1:
            Path(env["HZ_QUARANTINE_REPORT"]).write_text(
                json.dumps({"nodeid": TEST, "outcome": "failed"}) + "\nnot json\n"
            )
            return 1
        return broken(args, env)

    report = quarantine.run(entries, DAY, runner=first)
    assert report["entries"][0]["nodes"][TEST] == ["failed", "failed", "failed"]
    assert report["entries"][0]["status"] == "fail"


def test_the_main_run_writes_report_and_summary(tmp_path, monkeypatch, capsys):
    registry = tmp_path / "quarantine.toml"
    registry.write_text("")
    out = tmp_path / "out"
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert quarantine.main(["run"], on=DAY, registry=registry, out=out) == 0
    assert json.loads((out / "quarantine.json").read_text())["entries"] == []
    assert "No entries." in (out / "quarantine.md").read_text()
    step = tmp_path / "step-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step))
    assert quarantine.main(["run"], on=DAY, registry=registry, out=out) == 0
    assert "No entries." in step.read_text()
    capsys.readouterr()


def test_no_command_line_argument_names_a_file():
    with pytest.raises(SystemExit):
        quarantine.main(["--registry", "/etc/passwd", "check"], on=DAY)


class _Item:
    def __init__(self, nodeid: str) -> None:
        self.nodeid = nodeid
        self.markers: list[pytest.MarkDecorator] = []

    def add_marker(self, marker: pytest.MarkDecorator) -> None:
        self.markers.append(marker)


def _collect(mode: str, requested: tuple[str, ...] = (), on: dt.date = DAY):
    entries = quarantine.parse(entry_text())
    plugin = _quarantine.Quarantine(entries, on, mode, requested)
    items = [_Item(f"{TEST}[a]"), _Item("tests/unit/x_test.py::test_fine")]
    deselected: list[str] = []
    hook = SimpleNamespace(pytest_deselected=lambda items: deselected.extend(i.nodeid for i in items))
    plugin.pytest_collection_modifyitems(SimpleNamespace(hook=hook), items)
    return plugin, items, deselected


def test_the_required_jobs_leave_a_quarantined_test_out_and_name_it():
    plugin, items, deselected = _collect("")
    assert [item.nodeid for item in items] == ["tests/unit/x_test.py::test_fine"]
    assert deselected == plugin.deselected == [f"{TEST}[a]"]
    lines: list[str] = []
    plugin.pytest_terminal_summary(SimpleNamespace(write_line=lines.append), _options(None))
    assert lines[1].strip() == f"{TEST}[a]"
    assert plugin.pytest_report_header() == "quarantine: 1 entry in date, mode required"


def _options(workers: int | None) -> SimpleNamespace:
    return SimpleNamespace(option=SimpleNamespace(numprocesses=workers))


def test_the_xdist_controller_names_what_its_workers_leave_out_unless_it_was_asked_for():
    entries = quarantine.parse(entry_text())
    lines: list[str] = []
    _quarantine.Quarantine(entries, DAY, "").pytest_terminal_summary(
        SimpleNamespace(write_line=lines.append), _options(4)
    )
    assert lines == [
        "quarantine: workers leave out 1 entr(ies) of tests/quarantine.toml",
        f"  {TEST} (bead 3qj, until 2026-11-10)",
    ]
    lines.clear()
    _quarantine.Quarantine(entries, DAY, "", (TEST,)).pytest_terminal_summary(
        SimpleNamespace(write_line=lines.append), _options(4)
    )
    assert lines == []


def test_only_mode_keeps_just_the_quarantined_tests_and_marks_them():
    plugin, items, _ = _collect("only")
    assert [item.nodeid for item in items] == [f"{TEST}[a]"]
    assert items[0].markers[0].kwargs == {"bead": "3qj", "until": "2026-11-10"}
    assert plugin.deselected == []


def test_include_mode_a_named_node_and_an_overdue_entry_run_in_the_required_jobs():
    for plugin, items, _ in (_collect("include"), _collect("", (TEST,)), _collect("", (), dt.date(2026, 11, 11))):
        assert len(items) == 2
        assert plugin.deselected == []


def test_an_unknown_mode_is_refused():
    with pytest.raises(pytest.UsageError, match="HZ_QUARANTINE"):
        _quarantine.Quarantine([], DAY, "skip")


def test_the_report_records_failures_of_any_phase_and_call_results(tmp_path):
    log = tmp_path / "log.jsonl"
    plugin = _quarantine.Quarantine([], DAY, "only", report=log)
    for when, outcome in (("setup", "passed"), ("call", "passed"), ("teardown", "failed")):
        plugin.pytest_runtest_logreport(SimpleNamespace(nodeid=TEST, when=when, outcome=outcome))
    assert [json.loads(line)["when"] for line in log.read_text().splitlines()] == ["call", "teardown"]
    assert quarantine._outcomes(log) == {TEST: "failed"}


def test_a_malformed_registry_fails_every_run_closed(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "scripts" / "quarantine.py").write_text((ROOT / "scripts" / "quarantine.py").read_text())
    (tmp_path / "tests" / "quarantine.toml").write_text(entry_text(bead=""))
    config = SimpleNamespace(addinivalue_line=lambda *_: None, args=[])
    with pytest.raises(pytest.UsageError, match="breaks the quarantine rules"):
        _quarantine.configure(config, tmp_path, on=DAY)
    assert _quarantine.registry_module(tmp_path / "elsewhere") is None


def test_the_repository_registry_is_valid_and_its_lane_is_wired_into_ci():
    entries = quarantine.load()
    assert len(entries) <= quarantine.MAX_ENTRIES
    assert all(entry.test.split("::")[0] and (ROOT / entry.path).is_file() for entry in entries)
    jobs = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]
    assert "quarantine-registry" in jobs["ci-ok"]["needs"]
    assert {"quarantine", "quarantine-plan"}.isdisjoint(jobs["ci-ok"]["needs"])
    assert jobs["quarantine"]["name"] == "Quarantine (${{ matrix.os }})"
    assert jobs["quarantine"]["strategy"]["matrix"]["os"] == ["ubuntu-24.04", "macos-latest"]
    assert jobs["quarantine"]["permissions"] == {"contents": "read"}
    assert "github.event_name != 'pull_request'" in jobs["quarantine"]["if"]
    runs = [step.get("run") for step in jobs["quarantine-registry"]["steps"]]
    assert "make quarantine-check" in runs
    assert any(step.get("run") == "make test-quarantine" for step in jobs["quarantine"]["steps"])
    recipe = (ROOT / "Makefile").read_text(encoding="utf-8").split("\nquarantine-check:", 1)[1].split("\n\n", 1)[0]
    assert "@$(QUARANTINE) check" in recipe  # the required expiry check cannot become a no-op recipe
    assert "QUARANTINE := uv run --quiet --script scripts/quarantine.py\n" in (ROOT / "Makefile").read_text()
