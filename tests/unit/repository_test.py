"""Checks on files agents and tools read, so they never silently break."""

import json
import os
import re
import tomllib
from pathlib import Path

import pytest

from heinzel.modules.system.agent._service import parse_frontmatter

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
SKILLS = sorted([*REPO.glob("agent-skills/skills/*/SKILL.md"), *REPO.glob(".claude/skills/*/SKILL.md")])
AGENTS = sorted(REPO.glob(".claude/agents/*.md"))


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: str(p.relative_to(REPO)))
def test_skills_follow_the_agent_skills_specification(skill):
    meta = parse_frontmatter(skill.read_text(encoding="utf-8"))
    assert meta.get("name") == skill.parent.name, "name must match the directory"
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", meta["name"])
    assert len(meta["name"]) <= 64
    assert 0 < len(meta.get("description", "")) <= 1024


@pytest.mark.parametrize("agent", AGENTS, ids=lambda p: p.name)
def test_subagents_declare_name_description_tools_model_and_effort(agent):
    meta = parse_frontmatter(agent.read_text(encoding="utf-8"))
    assert meta.get("name") == agent.stem
    assert meta.get("description")
    assert meta.get("tools")
    assert meta.get("model") in {"haiku", "sonnet", "opus", "fable"}, "route work to a tier, see CLAUDE.md"
    assert meta.get("effort") in {"low", "medium", "high", "max"}


def test_there_is_something_to_check():
    assert len(SKILLS) >= 9
    assert AGENTS


def reviewers(review_skill: str, claude_md: str) -> set[str]:
    """Agents the review skill starts (its bullet list) and the CLAUDE.md routing table names, minus built-ins."""
    bullets = re.findall(r"^ +- (.*)$", review_skill, flags=re.MULTILINE)
    rows = [row.rsplit("|", 2)[-2] for row in re.findall(r"^\|.*\|$", claude_md, flags=re.MULTILINE)]
    names = [name for text in [*bullets, *rows] for name in re.findall(r"(?<!skill )`([a-z]+(?:-[a-z]+)*)`", text)]
    return set(names)


def test_finds_reviewers():
    skill = "1. Run `make check`.\n2. Start:\n   - `critic` and `test-engineer`, always;\n   - `architect` for `x`\n"
    claude = "| Work | Who |\n| --- | --- |\n| Search | built-in `Explore` |\n| Review | `critic`; skill `review` |\n"
    assert reviewers(skill, claude) == {"critic", "test-engineer", "architect", "x"}


def test_the_agent_roster_is_one_list():
    """Every agent file is started by the review skill or routed to in CLAUDE.md, and nothing else is named there."""
    named = reviewers(
        (REPO / ".claude" / "skills" / "review" / "SKILL.md").read_text(encoding="utf-8"),
        (REPO / "CLAUDE.md").read_text(encoding="utf-8"),
    )
    assert named == {agent.stem for agent in AGENTS}


def test_claude_plugin_marketplace_points_at_the_plugin():
    marketplace = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    [plugin] = marketplace["plugins"]
    manifest = json.loads((REPO / plugin["source"] / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == plugin["name"] == "heinzel"
    assert (REPO / plugin["source"] / "skills" / "hz-init" / "SKILL.md").is_file()


@pytest.mark.parametrize("root", [REPO, REPO / "template"], ids=["heinzel", "template"])
def test_claude_settings_hooks_exist(root):
    """Heinzelmännchen and every plugin scaffolded from the template ship the hook scripts their settings run."""
    settings = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    commands = [hook["command"] for event in settings["hooks"].values() for entry in event for hook in entry["hooks"]]
    assert commands
    for command in commands:
        script = re.search(r"\.claude/hooks/\w+\.py", command)
        assert script, command
        assert (root / script.group()).is_file(), f"{root.name}: {script.group()} is missing"


def test_first_party_plugins_match_the_plugins_directory():
    from heinzel.modules.system.plugin._service import FIRST_PARTY

    packages = {p.name.removeprefix("heinzel-") for p in (REPO / "plugins").iterdir() if p.is_dir()}
    assert packages == set(FIRST_PARTY)
    for name, description in FIRST_PARTY.items():
        project = tomllib.loads((REPO / "plugins" / f"heinzel-{name}" / "pyproject.toml").read_text(encoding="utf-8"))
        assert project["project"]["description"].startswith(description.removesuffix(".")), (
            f"`hz system plugin search` describes heinzel-{name} like its pyproject.toml does"
        )


@pytest.mark.parametrize("package", sorted((REPO / "plugins").iterdir()), ids=lambda p: p.name)
def test_first_party_plugins_are_released_tested_and_fenced_with_the_core(package):
    """Every first-party plugin follows the core's version, runs in its suite and coverage, and obeys the contracts,
    but the core distribution never depends on it: users choose it with `hz system plugin add` (ADR 0017, 0028)."""
    root = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    project = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    distribution, root_package = package.name, package.name.replace("-", "_")
    version = root["project"]["version"]
    assert project["name"] == distribution
    assert project["version"] == version
    assert project["dependencies"] == [f"heinzel>={version}"]
    repository = root["project"]["urls"]["Source"]
    assert project["urls"]["Source"] == f"{repository}/tree/main/plugins/{distribution}", "`hz --version` links it"
    assert project["urls"]["Issues"] == root["project"]["urls"]["Issues"]
    assert not any(r.startswith(distribution) for r in root["project"]["dependencies"]), "not installed by default"
    assert f"{distribution}>={version}" in root["dependency-groups"]["test"]
    assert root["tool"]["uv"]["sources"][distribution] == {"workspace": True}
    assert root_package in root["tool"]["coverage"]["run"]["source"]
    assert f"plugins/{distribution}/src" in root["tool"]["mypy"]["mypy_path"]
    assert root_package in root["tool"]["importlinter"]["root_packages"]
    contracts = {c["name"]: c for c in root["tool"]["importlinter"]["contracts"]}
    assert root_package in contracts["First-party plugins use only the public API, like any plugin"]["source_modules"]
    assert (
        root_package
        in contracts["First-party plugins are independent of each other, like separate repositories"]["modules"]
    )
    assert any(
        root_package in c.get("forbidden_modules", [])
        for c in contracts.values()
        if c.get("source_modules") == ["heinzel"]
    )
    bumped = {(f["filename"], f["search"]) for f in root["tool"]["bumpversion"]["files"]}
    assert (f"plugins/{distribution}/pyproject.toml", '"heinzel>={current_version}"') in bumped
    assert (
        f"plugins/{distribution}/pyproject.toml",
        f'name = "{distribution}"\nversion = "{{current_version}}"',
    ) in bumped
    assert ("pyproject.toml", f'"{distribution}>={{current_version}}"') in bumped


UNTRACKED = {
    ".git",
    ".venv",
    "worktrees",
    "__pycache__",
    ".hypothesis",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "dist",
}


def test_nothing_names_the_old_directory_of_first_party_plugins():
    """The maintainer named it `plugins/` ("it contains plugins"); only the changelog's history keeps the old name."""
    old = re.compile(r"(?<![\w-])" + "packages" + "/")  # split, so this file doesn't name it either
    offenders = []
    for directory, subdirectories, files in os.walk(REPO):
        subdirectories[:] = [d for d in subdirectories if d not in UNTRACKED]
        for name in files:
            path = Path(directory) / name
            if name in {"CHANGELOG.md", "uv.lock"} or path.is_symlink():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            offenders += [
                f"{path.relative_to(REPO)}:{n}" for n, line in enumerate(text.splitlines(), 1) if old.search(line)
            ]
    assert offenders == []


def test_every_system_submodule_is_independent_of_the_others():
    """ADR 0018: system's submodules could each be a plugin, so the import-linter contract must list every one."""
    contracts = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["importlinter"]
    name = "Every module could be a plugin: system's submodules don't import each other"
    (independence,) = [c for c in contracts["contracts"] if c["name"] == name]
    system = REPO / "src" / "heinzel" / "modules" / "system"
    packages = {f"heinzel.modules.system.{p.parent.name}" for p in system.glob("*/__init__.py")}
    assert set(independence["modules"]) == packages
