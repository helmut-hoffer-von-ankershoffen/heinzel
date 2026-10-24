"""Service of the agent module."""

import shutil
from importlib.resources import as_file, files
from pathlib import Path

from heinzel import HeinzelError, Progress
from heinzel.modules.system.agent._models import AgentSettings, InstalledSkill, Skill

SKILL_FILE = "SKILL.md"


def parse_frontmatter(text: str) -> dict[str, str]:
    """Read the flat `key: value` frontmatter of a SKILL.md; enough for name and description."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        key, sep, value = line.partition(":")
        if sep and not key.startswith(" "):
            fields[key.strip()] = value.strip()
    return {}


def bundled_skills_dir() -> Path:
    """Where the bundled skills are: inside the installed package, or `agent-skills/skills` in a checkout.

    Raises:
        HeinzelError: If the skills are not part of this installation.
    """
    with as_file(files("heinzel").joinpath("skills")) as packaged:
        if packaged.is_dir():
            return packaged
    source_tree = Path(__file__).resolve().parents[5] / "agent-skills" / "skills"
    if source_tree.is_dir():
        return source_tree
    msg = "Heinzelmännchen's skills are not packaged with this installation."
    raise HeinzelError(msg)


class AgentService:
    """Lists Heinzelmännchen's skills and installs them for coding agents."""

    def __init__(self, settings: AgentSettings, progress: Progress, skills_dir: Path | None = None) -> None:
        """Create the service; settings and progress are injected."""
        self._settings = settings
        self._progress = progress
        self._skills_dir = skills_dir

    def skills(self) -> list[Skill]:
        """The skills that ship with Heinzelmännchen."""
        root = self._skills_dir or bundled_skills_dir()
        skills: list[Skill] = []
        for skill_file in sorted(root.glob(f"*/{SKILL_FILE}")):
            meta = parse_frontmatter(skill_file.read_text(encoding="utf-8"))
            skills.append(
                Skill(
                    name=meta.get("name", skill_file.parent.name),
                    description=meta.get("description", ""),
                    path=skill_file.parent,
                )
            )
        return skills

    def install(self) -> list[InstalledSkill]:
        """Copy every skill into every configured skill directory, replacing older copies."""
        installed: list[InstalledSkill] = []
        skills = self.skills()
        with self._progress.task("Installing skills", total=len(skills) * len(self._settings.skill_dirs)) as advance:
            for skill_dir in self._settings.skill_dirs:
                base = skill_dir.expanduser()
                for skill in skills:
                    target = base / skill.name
                    if target.exists():
                        shutil.rmtree(target)
                    shutil.copytree(skill.path, target)
                    installed.append(InstalledSkill(name=skill.name, target=target))
                    advance(1)
        return installed

    def uninstall(self) -> list[InstalledSkill]:
        """Remove Heinzelmännchen's skills from every configured skill directory; other skills are left alone."""
        removed: list[InstalledSkill] = []
        for skill_dir in self._settings.skill_dirs:
            for skill in self.skills():
                target = skill_dir.expanduser() / skill.name
                if target.is_dir():
                    shutil.rmtree(target)
                    removed.append(InstalledSkill(name=skill.name, target=target))
        return removed
