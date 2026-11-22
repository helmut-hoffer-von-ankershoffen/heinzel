import json

import pytest
from rich.console import Console

from heinzel.heads.cli._progress import ConsoleProgress
from heinzel.heads.cli._steps import StepBoard
from heinzel.modules.system.updater._phases import Phases

pytestmark = pytest.mark.unit

PLAN = [("check", "Finding the newest version"), ("install", "Installing the update"), ("plugin:acme", "Plugin acme")]


def board_with(states: dict[str, str]) -> StepBoard:
    board = StepBoard(Console(record=True, width=100), "plain")
    for id_, label in PLAN:
        board.event({"id": id_, "label": label, "state": "pending"})
    for id_, state in states.items():
        board.event({"id": id_, "label": dict(PLAN)[id_], "state": state})
    return board


def test_the_bar_has_one_segment_per_step_and_counts_the_ended_ones():
    bar = board_with({"check": "done", "install": "running"}).bar()
    assert bar.plain == "━━━ ━━━ ━━━ 1/3"
    styles = [span.style for span in bar.spans if span.style]
    assert styles == ["green", "cyan", "bright_black"]


def test_a_step_line_counts_its_place_and_says_how_it_ended():
    board = board_with({"check": "done"})
    board.event({"id": "plugin:acme", "label": "Plugin acme", "state": "done", "detail": "Already newest (1.0)"})
    assert board.line("check").plain == "[1/3] Finding the newest version: done"
    assert board.line("plugin:acme").plain == "[3/3] Plugin acme: done (Already newest (1.0))"
    assert board.line("plugin:acme", mark=True).plain == "  ✓ [3/3] Plugin acme: done (Already newest (1.0))"


def test_plain_output_prints_a_line_when_a_step_starts_and_ends_but_none_for_the_plan():
    console = Console(record=True, width=100)
    board = StepBoard(console, "plain")
    for id_, label in PLAN:
        board.event({"id": id_, "label": label, "state": "pending"})
    board.event({"id": "check", "label": "Finding the newest version", "state": "running"})
    board.event({"id": "check", "label": "Finding the newest version", "state": "running"})  # told twice, once
    board.event({"id": "check", "label": "Finding the newest version", "state": "failed", "detail": "offline"})
    assert console.export_text().splitlines() == [
        "[1/3] Finding the newest version ...",
        "[1/3] Finding the newest version: failed (offline)",
    ]


def test_json_mode_writes_every_event_as_one_json_line():
    console = Console(record=True, width=40)
    board = StepBoard(console, "json")
    board.event({"id": "check", "label": "Finding the newest version — long label", "state": "running"})
    [line] = console.export_text().splitlines()
    assert json.loads(line) == {
        "event": "phase",
        "id": "check",
        "label": "Finding the newest version — long label",
        "state": "running",
    }


def test_the_console_progress_shows_the_phases_of_an_operation_live_and_ends_them(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("CI", raising=False)
    console = Console(record=True, force_terminal=True, force_interactive=True, width=100)
    progress = ConsoleProgress(console)
    phases = Phases(progress, PLAN, operation="system.update")
    with phases.step("check", "Finding the newest version"), progress.task("Asking GitHub", total=2) as advance:
        advance(1)  # joins the board: one live display, never a second one
    phases.note("install", "Installing the update", "skipped", "Nothing newer to install.")
    progress.finish()
    text = console.export_text()
    assert "✓ [1/3] Finding the newest version: done" in text
    assert "- [2/3] Installing the update: skipped (Nothing newer to install.)" in text


def test_json_progress_keeps_the_phases_off_stdout_and_as_json_lines():
    console = Console(record=True, width=100)
    progress = ConsoleProgress(console)
    progress.enabled, progress.as_json = False, True
    Phases(progress, PLAN[:1]).note("check", "Finding the newest version", "done")
    progress.finish()
    states = [json.loads(line)["state"] for line in console.export_text().splitlines()]
    assert states == ["pending", "done"]


def test_a_checklist_draws_every_row_in_place_and_hides_the_skipped_ones():
    console = Console(record=True, width=100)
    board = StepBoard(console, "live")
    rows = [
        ("check", "hz"),
        ("plugin:acme", "heinzel-acme"),
        ("plugin:old", "heinzel-old"),
        ("daemon", "Daemon, web server"),
    ]
    for id_, label in rows:
        board.event({"id": id_, "label": label, "state": "pending", "checklist": True})
    board.event({"id": "check", "label": "hz", "state": "done", "detail": "0.9.51 → 0.9.52", "checklist": True})
    board.event({"id": "plugin:old", "label": "heinzel-old", "state": "skipped", "checklist": True})
    board.event(
        {"id": "plugin:acme", "label": "heinzel-acme", "state": "warn", "detail": "held back", "checklist": True}
    )
    board.event({"id": "daemon", "label": "Daemon, web server", "state": "running", "checklist": True})
    try:
        console.print(board.checklist(list(board._steps.values())))
    finally:
        board.close()
    lines = [line.rstrip() for line in console.export_text().splitlines() if line.strip()]
    assert lines[0] == "✓ hz                         0.9.51 → 0.9.52"
    assert lines[1] == "! heinzel-acme               held back"
    assert "heinzel-old" not in "\n".join(lines)
    assert lines[2].endswith("Daemon, web server")
    assert lines[3].endswith("2/3 Daemon, web server")  # the overall bar: skipped steps are not counted
    assert len(lines) == 4


def _checklist_board(mode: str, console: Console) -> StepBoard:
    board = StepBoard(console, mode)
    for id_, label in PLAN:
        board.event({"id": id_, "label": label, "state": "pending", "checklist": True})
    board.event({"id": "check", "label": PLAN[0][1], "state": "done", "checklist": True})
    board.event({"id": "install", "label": PLAN[1][1], "state": "running", "checklist": True})
    return board


def test_a_terminal_checklist_has_an_overall_bar_with_steps_done_and_the_current_step():
    console = Console(record=True, width=100, force_terminal=True)
    text = Console(record=True, width=100)
    text.print(StepBoard.checklist(list(_checklist_board("live", console)._steps.values())))
    last = text.export_text().splitlines()[-1]
    assert "1/3" in last
    assert "Installing the update" in last
    assert "━" in last


def test_plain_and_json_modes_draw_no_bar_only_lines_and_json_events():
    plain = Console(record=True, width=100)
    _checklist_board("plain", plain)
    out = plain.export_text()
    assert "━" not in out
    assert "\x1b" not in out
    assert out.splitlines() == [
        "[1/3] Finding the newest version: done",
        "[2/3] Installing the update ...",
    ]
    machine = Console(record=True, width=100)
    _checklist_board("json", machine)
    events = [json.loads(line) for line in machine.export_text().splitlines()]
    assert {event["event"] for event in events} == {"phase"}
    assert "━" not in machine.export_text()
