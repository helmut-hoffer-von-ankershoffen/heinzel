"""Checks on files agents and tools read, so they never silently break."""

import json
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


def test_first_party_plugins_match_the_packages_directory():
    from heinzel.modules.system.plugin._service import FIRST_PARTY

    packages = {p.name.removeprefix("heinzel-") for p in (REPO / "packages").iterdir() if p.is_dir()}
    assert packages == set(FIRST_PARTY)
    for name, description in FIRST_PARTY.items():
        project = tomllib.loads((REPO / "packages" / f"heinzel-{name}" / "pyproject.toml").read_text(encoding="utf-8"))
        assert project["project"]["description"].startswith(description.removesuffix(".")), (
            f"`hz system plugin search` describes heinzel-{name} like its pyproject.toml does"
        )
