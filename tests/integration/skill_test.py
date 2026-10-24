"""Skills through the CLI (ADR 0060): list, show, export for Claude Code, sync and unexport."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from heinzel import Config, Kernel, Module, ModuleRecord, Settings
from heinzel.heads.cli import build
from heinzel.kernel import Origin, discover

pytestmark = pytest.mark.integration

PLUGIN = Origin(distribution="heinzel-notes", version="1.0.0", builtin=False)


def kernel(settings: Settings, tmp_path: Path, description: str = "Review meeting notes.") -> Kernel:
    directory = tmp_path / f"src-{abs(hash(description))}" / "review-notes"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: review-notes\ndescription: {description}\nallowed-tools: Bash\n---\n\n# Review\n", encoding="utf-8"
    )
    module = Module("notes", "Notes.")
    module.skill(directory)
    records = [*discover(plugins=False)[0], ModuleRecord(qualname="notes", module=module, origin=PLUGIN)]
    data = {"system": {"skill": {"export_dirs": [str(tmp_path / "claude")], "personal_dir": str(tmp_path / "mine")}}}
    return Kernel(settings, records, config=Config(files=(), data=data))


def hz(k: Kernel, *args: str) -> tuple[int, object]:
    result = CliRunner().invoke(build(k), [*args, "--json"])
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, result.output


def test_list_show_export_sync_and_unexport(settings, tmp_path):
    k = kernel(settings, tmp_path)
    code, listed = hz(k, "system", "skill", "list")
    assert code == 0, listed
    assert {(s["name"], s["source"]) for s in listed["skills"]} == {
        ("explain-heinzel", "core"),
        ("review-notes", "plugin"),
    }
    code, shown = hz(k, "system", "skill", "show", "review-notes")
    assert (code, shown["body"], shown["skill"]["hints"]) == (0, "# Review\n", {"allowed-tools": "Bash"})
    code, exported = hz(k, "system", "skill", "export", "--name", "review-notes")
    assert code == 0, exported
    target = tmp_path / "claude" / "heinzel-review-notes" / "SKILL.md"
    assert "allowed-tools" not in target.read_text(encoding="utf-8")
    code, synced = hz(kernel(settings, tmp_path, "Changed."), "system", "skill", "export", "--sync")
    assert (code, [i["action"] for i in synced["items"]]) == (0, ["changed"])
    assert not target.exists()
    hz(k, "system", "skill", "export", "--all-skills")
    code, removed = hz(k, "system", "skill", "unexport", "--all-skills")
    assert (code, sorted(i["name"] for i in removed["items"])) == (0, ["explain-heinzel", "review-notes"])


def test_a_conflict_exits_1_and_show_of_an_unknown_skill_says_so(settings, tmp_path):
    k = kernel(settings, tmp_path)
    (tmp_path / "claude" / "heinzel-review-notes").mkdir(parents=True)
    code, result = hz(k, "system", "skill", "export", "--name", "review-notes")
    assert (code, result["items"][0]["action"]) == (1, "conflict")
    code, output = hz(k, "system", "skill", "show", "gone")
    assert code == 1
    assert "No skill is called 'gone'" in str(output)


def test_human_output_lists_and_shows(settings, tmp_path):
    k = kernel(settings, tmp_path)
    listed = CliRunner().invoke(build(k), ["system", "skill", "list"]).output
    assert "review-notes" in listed
    assert "heinzel-notes (plugin)" in listed
    shown = CliRunner().invoke(build(k), ["system", "skill", "show", "review-notes"]).output
    assert "Hints, not applied by hz: allowed-tools: Bash" in shown
    exported = CliRunner().invoke(build(k), ["system", "skill", "export", "--name", "review-notes"]).output
    assert "exported  review-notes" in exported
