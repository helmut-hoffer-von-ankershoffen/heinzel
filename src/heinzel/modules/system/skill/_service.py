"""Service of the skill module: the skills in effect along the layer stack, read-only, and the Claude Code export."""

from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path

from heinzel import HeinzelError, Kernel, LayerStack, ModuleRecord, Skill, SkillEntry, SkillLet, Skills
from heinzel.kernel import (
    MAX_SKILL_LOAD,
    LoadedSkill,
    SkillSource,
    read_skill,
    read_skill_file,
    skill_text,
    user_config_dir,
)
from heinzel.modules.system.skill._export import Exporter
from heinzel.modules.system.skill._models import (
    ExportResult,
    SkillDetail,
    SkillList,
    SkillSettings,
    SkillView,
)

PERSONAL = "personal"


@dataclass(frozen=True, slots=True)
class Offered:
    """A skill in effect under its name: where it comes from and what it hides."""

    skill: Skill
    directory: Path
    source: SkillSource
    record: ModuleRecord | None
    shadows: list[str] = field(default_factory=list[str])

    @property
    def module(self) -> str | None:
        """The module offering it; None for a personal skill."""
        return self.record.qualname if self.record else None


@dataclass(frozen=True, slots=True)
class Catalog:
    """The skills in effect by name, the names nobody decided between, and personal skills that failed a check."""

    skills: dict[str, Offered]
    conflicts: list[str]
    problems: list[str]


def _branch(stack: LayerStack, layer: str) -> str | None:
    """`site` for the site, `org` for a layer beside it on the org line, None for the company and the team."""
    if layer == stack.site:
        return "site"
    if stack.site is None or layer in stack.site_ancestors or layer == stack.layer:
        return None
    return "org"


class SkillService(Skills):
    """The skills every AI path reads (ADR 0061): the core's, every plugin's and layer's, and the person's own."""

    def __init__(self, kernel: Kernel, settings: SkillSettings) -> None:
        """Create the service; the kernel and the settings are injected."""
        self._kernel = kernel
        self._settings = settings

    # The catalog.

    def personal_dir(self) -> Path:
        """Where the person's own skills are."""
        return (self._settings.personal_dir or user_config_dir() / "skills").expanduser()

    def export_dirs(self) -> list[Path]:
        """Where exports go, expanded."""
        return [directory.expanduser() for directory in self._settings.export_dirs]

    def catalog(self) -> Catalog:
        """Each skill by name; of several offering one name, the most specific source wins.

        General to specific: a plugin outside the person's stack, the core, a layer of the stack (the team over the
        site and the orgs, those over the company), the person's own directory. So a plugin outside the stack never
        hides a core skill. An org and the site offering one name, with nothing more specific offering it, is a
        conflict nobody decided (ADR 0040): the name is left out and reported.
        """
        stack = self._kernel.layer_stack()
        rank = {name: index for index, name in enumerate(stack.names)}
        candidates: list[tuple[tuple[int, int], Offered]] = []
        for record, let in self._kernel.lets(SkillLet):
            top = record.path[0]
            if top in rank:
                candidates.append(((2, rank[top]), Offered(let.skill, let.directory, "layer", record)))
            elif record.origin.builtin:
                candidates.append(((1, 0), Offered(let.skill, let.directory, "core", record)))
            else:
                candidates.append(((0, 0), Offered(let.skill, let.directory, "plugin", record)))
        personal, problems = self._personal()
        candidates += [((3, 0), offered) for offered in personal]
        by_name: dict[str, list[tuple[tuple[int, int], Offered]]] = {}
        for priority, offered in sorted(candidates, key=lambda pair: (-pair[0][0], -pair[0][1], pair[1].module or "")):
            by_name.setdefault(offered.skill.name, []).append((priority, offered))
        skills: dict[str, Offered] = {}
        conflicts: list[str] = []
        for name in sorted(by_name):
            (_, winner), *others = by_name[name]
            if winner.source == "layer" and winner.record is not None:
                branch = _branch(stack, winner.record.path[0])
                rivals = [
                    o.record.path[0]
                    for _, o in others
                    if o.source == "layer" and o.record and _branch(stack, o.record.path[0]) not in {None, branch}
                ]
                if branch is not None and rivals:
                    conflicts.append(
                        f"{name}: offered by {winner.record.path[0]} and {', '.join(rivals)}, an org and the site; "
                        f"nobody decided which applies, so neither does until {stack.layer or 'your team'} offers it"
                    )
                    continue
            shadows = [o.module or PERSONAL for _, o in others]
            skills[name] = Offered(winner.skill, winner.directory, winner.source, winner.record, shadows)
        return Catalog(skills, conflicts, problems)

    def _personal(self) -> tuple[list[Offered], list[str]]:
        """The person's own skills, each checked like a plugin's; one that fails is a problem, not an error."""
        root = self.personal_dir()
        if not root.is_dir():
            return [], []
        resolved = root.resolve()
        for target in self.export_dirs():
            if resolved == target.resolve() or resolved.is_relative_to(target.resolve()):
                return [], [f"{root} is inside {target}, where exports go; keep your own skills elsewhere"]
        offered: list[Offered] = []
        problems: list[str] = []
        try:
            directories = sorted(root.iterdir())
        except OSError as exc:
            return [], [f"{root} can't be listed ({exc.strerror or type(exc).__name__})"]
        for directory in directories:
            if directory.name.startswith(".") or not directory.is_dir():
                continue
            try:
                offered.append(Offered(read_skill(directory), directory, "personal", None))
            except ValueError as exc:
                problems.append(str(exc))
        return offered, problems

    def chat_allowed(self) -> list[str]:
        """The skills the chat may use: `[system.skill] chat`, or the core's when it isn't set."""
        if self._settings.chat is not None:
            return list(self._settings.chat)
        return sorted(name for name, offered in self.catalog().skills.items() if offered.source == "core")

    # Skills, read-only.

    def index(self, allowed: Collection[str] | None = None) -> list[SkillEntry]:
        """The skills in effect that `allowed` names, in name order."""
        return [
            self._entry(offered)
            for name, offered in self.catalog().skills.items()
            if allowed is None or name in allowed
        ]

    def load(self, name: str, allowed: Collection[str] | None = None) -> LoadedSkill:
        """The body of skill `name`, cleaned and cut, with where it comes from.

        Raises:
            HeinzelError: If `allowed` doesn't name it or there is no such skill.
        """
        offered = self._offered(name, allowed)
        body = skill_text(offered.skill.body.encode("utf-8"))
        return LoadedSkill(
            name=name,
            source=offered.source,
            module=offered.module,
            fingerprint=offered.skill.fingerprint,
            body=body[:MAX_SKILL_LOAD] + ("\n…(cut)" if len(body) > MAX_SKILL_LOAD else ""),
            files=[file.path for file in offered.skill.files],
        )

    def read_file(self, name: str, path: str, allowed: Collection[str] | None = None) -> str:
        """The text of file `path` of skill `name`, cleaned; only a path the skill lists.

        Raises:
            HeinzelError: If `allowed` doesn't name the skill, there is no such skill or file, or the file changed.
        """
        offered = self._offered(name, allowed)
        try:
            return skill_text(read_skill_file(offered.directory, offered.skill, path))
        except ValueError as exc:
            raise HeinzelError(str(exc)) from exc

    def _offered(self, name: str, allowed: Collection[str] | None) -> Offered:
        if allowed is not None and name not in allowed:
            msg = f"Skill {name!r} isn't one this may use; it may use {', '.join(sorted(allowed)) or 'none'}."
            raise HeinzelError(msg)
        offered = self.catalog().skills.get(name)
        if offered is None:
            msg = f"No skill is called {name!r}; `hz system skill list` shows the skills in effect."
            raise HeinzelError(msg)
        return offered

    @staticmethod
    def _entry(offered: Offered) -> SkillEntry:
        return SkillEntry(
            name=offered.skill.name,
            description=offered.skill.description,
            source=offered.source,
            module=offered.module,
            fingerprint=offered.skill.fingerprint,
            files=[file.path for file in offered.skill.files],
            hints=dict(offered.skill.hints),
        )

    # Operations.

    def listing(self) -> SkillList:
        """Every skill in effect, with what hides what, the chat's set and where each is exported."""
        catalog = self.catalog()
        chat = set(self.chat_allowed())
        exported = self._exporter().exported()
        views = [self._view(offered, chat, exported) for offered in catalog.skills.values()]
        next_steps = [f"hz system skill show {views[0].name}"] if views else []
        if views and not any(view.exported for view in views):
            next_steps.append(f"hz system skill export --name {views[0].name}")
        return SkillList(skills=views, conflicts=catalog.conflicts, problems=catalog.problems, next=next_steps)

    def show(self, name: str) -> SkillDetail:
        """One skill with its body.

        Raises:
            HeinzelError: If there is no such skill.
        """
        offered = self._offered(name, None)
        view = self._view(offered, set(self.chat_allowed()), self._exporter().exported())
        return SkillDetail(skill=view, body=self.load(name).body)

    def export(self, names: Collection[str], *, everything: bool = False, sync: bool = False) -> ExportResult:
        """Export the named skills (or every one but your own) for Claude Code; with `sync`, only remove stale ones.

        Raises:
            HeinzelError: If nothing is named, a name is unknown, or `sync` comes with names.
        """
        catalog = self.catalog()
        if sync:
            if names or everything:
                msg = "`--sync` only removes stale exports; run it without names or `--all-skills`."
                raise HeinzelError(msg)
            return self._exporter().sync(catalog.skills)
        if everything:
            chosen = [name for name, offered in catalog.skills.items() if offered.source != "personal"]
        elif names:
            if unknown := sorted(set(names) - set(catalog.skills)):
                msg = f"No skill is called {', '.join(unknown)}; `hz system skill list` shows the skills in effect."
                raise HeinzelError(msg)
            chosen = sorted(set(names))
        else:
            msg = "Name the skills to export (`--name review-pull-request`), or pass `--all-skills`."
            raise HeinzelError(msg)
        return self._exporter().export([catalog.skills[name] for name in chosen])

    def unexport(self, names: Collection[str], *, everything: bool = False) -> ExportResult:
        """Remove exported skills: the named ones, or every one hz exported.

        Raises:
            HeinzelError: If nothing is named.
        """
        if not names and not everything:
            msg = "Name the skills to remove from the export (`--name review-pull-request`), or pass `--all-skills`."
            raise HeinzelError(msg)
        return self._exporter().remove(None if everything else set(names))

    def _exporter(self) -> Exporter:
        return Exporter(self.export_dirs(), self._kernel.settings.state_dir)

    @staticmethod
    def _view(offered: Offered, chat: set[str], exported: dict[str, list[Path]]) -> SkillView:
        return SkillView(
            name=offered.skill.name,
            description=offered.skill.description,
            source=offered.source,
            module=offered.module,
            plugin=offered.record.origin.distribution if offered.record and not offered.record.origin.builtin else None,
            fingerprint=offered.skill.fingerprint,
            files=[file.path for file in offered.skill.files],
            scripts=sum(1 for file in offered.skill.files if file.kind == "script"),
            hints=dict(offered.skill.hints),
            shadows=offered.shadows,
            chat=offered.skill.name in chat,
            exported=[str(path) for path in exported.get(offered.skill.name, [])],
        )
