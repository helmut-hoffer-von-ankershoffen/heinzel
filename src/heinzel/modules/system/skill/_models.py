"""Models and settings of the skill module."""

from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from heinzel import ModuleSettings

DEFAULT_EXPORT_DIR = Path("~/.claude/skills")


class SkillSettings(ModuleSettings):
    """Which skills the chat may use, your own skills and where exports go: the `[system.skill]` table.

    User scope: a project `hz.toml` can set none of these, because a repository someone else controls must not add
    prompt text or pick a directory hz writes into (ADR 0061).
    """

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"chat", "personal_dir", "export_dirs"})

    chat: list[str] | None = Field(
        default=None,
        title="Skills the chat may use",
        description="The skills the chat may load, by name; when not set, the core's own skills.",
        json_schema_extra={"x-expert": True},
    )
    personal_dir: Path | None = Field(
        default=None,
        title="Your own skills",
        description="A folder of your own skills, each a folder with a SKILL.md; they win over every plugin's. "
        "Defaults to a skills folder in your configuration folder.",
        json_schema_extra={"x-expert": True},
    )
    export_dirs: list[Path] = Field(
        default_factory=lambda: [DEFAULT_EXPORT_DIR],
        title="Where skills are exported",
        description="The folders chosen skills are exported into, one per coding agent, each named after the skill "
        "with heinzel- before it.",
        json_schema_extra={"x-expert": True},
    )


class SkillView(BaseModel):
    """A skill in effect: what it is, where it comes from, and what it hides."""

    name: str = Field(description="The skill's name, e.g. `review-pull-request`.")
    description: str = Field(description="When to use it, in one line.")
    source: str = Field(description="`core`, `plugin`, `layer` or `personal`.")
    module: str | None = Field(default=None, description="The module offering it; none for a personal skill.")
    plugin: str | None = Field(default=None, description="The plugin's distribution; none for the core and yours.")
    fingerprint: str = Field(description="SHA-256 over its files.")
    files: list[str] = Field(default_factory=list[str], description="Paths of the files beside its SKILL.md.")
    scripts: int = Field(default=0, description="How many of them are scripts, which hz never runs.")
    hints: dict[str, str] = Field(
        default_factory=dict[str, str], description="Frontmatter hints such as `allowed-tools`, never applied."
    )
    shadows: list[str] = Field(
        default_factory=list[str], description="Modules (or `personal`) whose skill of the same name this one hides."
    )
    chat: bool = Field(default=False, description="Whether the chat may use it (`[system.skill] chat`).")
    exported: list[str] = Field(
        default_factory=list[str], description="Directories it is exported to, from `hz system skill export`."
    )


class SkillList(BaseModel):
    """Every skill in effect, the names nobody decided between, and personal skills that failed their checks."""

    skills: list[SkillView] = Field(description="The skills in effect, by name.")
    conflicts: list[str] = Field(default_factory=list[str], description="Names left out: an org and the site both.")
    problems: list[str] = Field(default_factory=list[str], description="Your skills that failed a check, and why.")
    next: list[str] = Field(default_factory=list[str], description="Commands worth running next.")


class SkillDetail(BaseModel):
    """One skill with its body, as `hz system skill show` prints it."""

    skill: SkillView = Field(description="The skill.")
    body: str = Field(description="Its body, cleaned, without the frontmatter.")


ExportAction = Literal["exported", "removed", "conflict", "changed"]


class ExportItem(BaseModel):
    """What an export did with one skill in one directory."""

    name: str = Field(description="The skill.")
    action: ExportAction = Field(
        description="`exported`; `removed` (gone or unexported); `changed` (removed: export again to review it); "
        "`conflict` (left alone, see `detail`)."
    )
    target: Path = Field(description="The directory written, removed or left alone.")
    detail: str = Field(default="", description="Why, for a conflict or a change.")


class ExportResult(BaseModel):
    """What `hz system skill export` or `unexport` did."""

    items: list[ExportItem] = Field(default_factory=list[ExportItem], description="One per skill and directory.")
    next: list[str] = Field(default_factory=list[str], description="Commands worth running next.")
