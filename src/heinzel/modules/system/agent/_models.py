"""Models of the agent module."""

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from heinzel import ModuleSettings


class AgentSettings(ModuleSettings):
    """Settings of the agent module: the `[system.agent]` table or `HZ_SYSTEM_AGENT__*` variables."""

    section_title: ClassVar[str | None] = "Agent skills"  # the heading in Settings

    skill_dirs: list[Path] = Field(
        default_factory=lambda: [Path("~/.claude/skills")],
        title="Skill folders",
        description="Folders Heinzelmännchen's skills are copied into when you install them for "
        "your coding agents, one per agent.",
        json_schema_extra={"x-expert": True},
    )


class Skill(BaseModel):
    """An Agent Skill (agentskills.io) that ships with Heinzelmännchen."""

    name: str = Field(description="Skill name, as in its SKILL.md front matter.")
    description: str = Field(description="When an agent should use the skill.")
    path: Path = Field(description="Directory containing SKILL.md.")


class InstalledSkill(BaseModel):
    """A skill copied into an agent's skill directory."""

    name: str = Field(description="Skill name.")
    target: Path = Field(description="Directory the skill was copied to or removed from.")
