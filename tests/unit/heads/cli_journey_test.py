"""Answers files and the setup state file of `hz setup` (ADR 0032, amended)."""

import json
import stat
import tomllib

import pytest

from heinzel import HeinzelError
from heinzel.heads.cli._journey import SetupState, answers_toml, load_answers

pytestmark = pytest.mark.unit


def test_answers_are_read_by_full_key(tmp_path):
    path = tmp_path / "answers.toml"
    path.write_text('[org]\nrole = "engineer"\n\n[system.daemon]\ninstall = true\ninterval = 5\n', encoding="utf-8")
    assert load_answers(path) == {"org.role": "engineer", "system.daemon.install": True, "system.daemon.interval": 5}


def test_an_unreadable_or_invalid_answers_file_names_the_file(tmp_path):
    with pytest.raises(HeinzelError, match=r"Can't read the answers file .*missing.toml"):
        load_answers(tmp_path / "missing.toml")
    broken = tmp_path / "broken.toml"
    broken.write_text("[org\n", encoding="utf-8")
    with pytest.raises(HeinzelError, match=r"broken.toml is not valid TOML"):
        load_answers(broken)


def test_answers_are_written_back_as_a_file_that_reads_the_same(tmp_path):
    answers = {"org.role": "engineer", "org.working_hours": [9, 17], "system.daemon.install": True}
    text = answers_toml(answers)
    assert text.startswith("# Answers for `hz setup --answers <file>`; never secrets")
    assert tomllib.loads(text) == {
        "org": {"role": "engineer", "working_hours": [9, 17]},
        "system": {"daemon": {"install": True}},
    }
    path = tmp_path / "answers.toml"
    path.write_text(text, encoding="utf-8")
    assert load_answers(path) == answers


def test_finished_steps_are_kept_privately_and_read_back(tmp_path):
    state = SetupState.load(tmp_path)
    state.record("org", "done", {"org.role": "engineer"})
    state.record("clock", "skipped", {})
    assert stat.S_IMODE((tmp_path / "setup.json").stat().st_mode) == 0o600
    again = SetupState.load(tmp_path)
    assert list(again.steps) == ["org", "clock"]
    assert again.steps["org"].outcome == "done"
    assert again.answers() == {"org.role": "engineer"}
    assert json.loads((tmp_path / "setup.json").read_text(encoding="utf-8"))["version"] == 1


def test_restart_forgets_every_step(tmp_path):
    state = SetupState.load(tmp_path)
    state.record("org", "done", {})
    state.clear()
    assert not (tmp_path / "setup.json").exists()
    assert SetupState.load(tmp_path).steps == {}
    state.clear()  # nothing to forget is fine too


@pytest.mark.parametrize("content", ["not json", "[]", '{"steps": {"org": {}}}', '{"steps": 3}'])
def test_an_unreadable_state_starts_over(tmp_path, content):
    (tmp_path / "setup.json").write_text(content, encoding="utf-8")
    assert SetupState.load(tmp_path).steps == {}
