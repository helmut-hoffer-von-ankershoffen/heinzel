"""Playbooks: what a plugin contributes besides code, so the core stays generic (ADR 0037).

A playbook names the **asset types** a team keeps (a weekly report, a team tracker), how to find candidates for each
(**discovery hints**: capability reads the person can already make, narrowed by a title pattern), **template
contracts** that keep those assets true, and **recommended configuration**. It is data: the kernel checks its shape
and collects it; the module that owns contracts (the built-in `workflow`) evaluates the hints, offers the templates
(`hz workflow new --template`) and applies a recommended setting only when the person accepts it (`hz <module> setup`,
`hz setup`). Nothing in a playbook runs by itself: hints only read, a template can't carry an active schedule, and
defaults are proposals limited to capability bindings and the module's own settings.
"""

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from heinzel.kernel._capabilities import CAPABILITIES
from heinzel.kernel._config import ConfigEditor
from heinzel.kernel._duration import parse_window
from heinzel.kernel._lets import Let
from heinzel.kernel._prompter import Prompter
from heinzel.kernel._text import clean_text

SLUG = re.compile(r"^[a-z](?:-?[a-z0-9])*$")
"""Asset type and template names: lowercase words joined by single dashes, like contract names."""
READ = re.compile(r"^[a-z][a-z0-9-]*\.[a-z][a-z0-9_]*$")
SETTING = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$")
"""A recommended setting's key, as `hz system config set` takes it: `<section>.<key>`, e.g. `capabilities.tracker`."""
TEMPLATE_SUFFIX = ".prose.md"
PLACEHOLDER = "{{asset:%s}}"
"""How a template contract marks where a found asset's reference goes, e.g. `{{asset:weekly-report}}`."""
MAX_SLUG = 64
_PLACEHOLDER = re.compile(r"\{\{asset:([a-z0-9-]+)\}\}")
_QUOTED = re.compile(r'"[^"\n]*"')
_SCHEDULE = re.compile(r"^\s*schedule\s*:", re.MULTILINE)


def _slug(kind: str, name: str) -> None:
    if not SLUG.fullmatch(name) or len(name) > MAX_SLUG:
        msg = f"{kind} name {name!r} must be lowercase words joined by single dashes, at most {MAX_SLUG} characters"
        raise ValueError(msg)


@dataclass(frozen=True, kw_only=True, slots=True)
class DiscoveryHint:
    """Where candidates for an asset type may be: one capability read, and a pattern their titles match.

    The read is a contract input's shape (`read`, `args`, `window`) and is checked the same way when it runs. Every
    item it returns whose `title` matches `match` is a candidate; its reference is its `ref`, `key`, `id` or `url`,
    whichever it has first.
    """

    read: str
    """A capability read method, `<capability>.<method>`, e.g. `tracker.issues_updated`."""
    args: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    """The method's arguments besides its time window, e.g. `{"project": "APP"}`."""
    window: str | None = None
    """For methods that take a time window: back from now, `30d` or `last 30d`, or ahead, `next 14d`; at most 366d."""
    match: str | None = None
    """A regular expression the title must match (searched, case-sensitive unless it says `(?i)`); all if None."""
    summary: str = ""
    """What it looks for, in words people read in a proposal, e.g. "issues titled Weekly report"."""

    def __post_init__(self) -> None:
        """Refuse a read that isn't a known capability's read method, a bad window or pattern.

        Raises:
            ValueError: If the read, window or pattern is invalid.
        """
        capability, _, method = self.read.partition(".")
        spec = CAPABILITIES.get(capability)
        if not READ.fullmatch(self.read) or spec is None or spec.methods.get(method) != "read":
            msg = f"Discovery hint {self.read!r} must name a capability's read method, e.g. tracker.issues_updated"
            raise ValueError(msg)
        if capability == "agent-runner":
            msg = "Discovery hints read data; agent-runner.run is not a hint"
            raise ValueError(msg)
        if self.window is not None:
            parse_window(self.window)
        if self.match is not None:
            try:
                re.compile(self.match)
            except re.error as exc:
                msg = f"Discovery hint {self.read!r}: {self.match!r} isn't a regular expression: {exc}"
                raise ValueError(msg) from exc
        object.__setattr__(self, "args", MappingProxyType(dict(self.args)))


@dataclass(frozen=True, kw_only=True, slots=True)
class AssetType:
    """A kind of thing a team keeps true, such as its weekly report, with where to look for it."""

    name: str
    """Lowercase words joined by dashes, e.g. `weekly-report`; templates refer to it as `{{asset:<name>}}`."""
    description: str
    """What it is, for the person choosing which of the candidates it is."""
    hints: tuple[DiscoveryHint, ...] = ()
    kept_by: str | None = None
    """The playbook's template whose output keeps this asset true, e.g. a rolling report document kept by the
    weekly report contract. Drafts are local today; the approved version is read from where the asset lives."""

    def __post_init__(self) -> None:
        """Refuse a bad name or an empty description.

        Raises:
            ValueError: If the name isn't a slug or the description is empty.
        """
        _slug("Asset type", self.name)
        if not self.description.strip():
            msg = f"Asset type {self.name!r} needs a description; people choose candidates by it"
            raise ValueError(msg)
        object.__setattr__(self, "hints", tuple(self.hints))


@dataclass(frozen=True, kw_only=True, slots=True)
class ContractTemplate:
    """A workflow contract a playbook offers: the text of a `<name>.prose.md`, with `{{asset:<type>}}` placeholders."""

    name: str
    text: str

    def __post_init__(self) -> None:
        """Refuse a bad name, an empty text, an active schedule and placeholders outside double-quoted strings.

        A schedule is the person's decision, never a plugin's: a template that had one would run on the daemon the
        moment it is written. A placeholder belongs in a double-quoted YAML string in the frontmatter, where a filled
        reference stays one string; anywhere else it could add keys or reach the agent's instructions.

        Raises:
            ValueError: If the template breaks one of these rules.
        """
        _slug("Template", self.name)
        if not self.text.strip():
            msg = f"Template {self.name!r} is empty"
            raise ValueError(msg)
        frontmatter, body = _split(self.text)
        if _SCHEDULE.search(frontmatter):
            msg = f"Template {self.name!r} has an active schedule; comment it out: the person decides when it runs"
            raise ValueError(msg)
        if _PLACEHOLDER.search(body):
            msg = f"Template {self.name!r} has a placeholder outside the frontmatter; put them in its input arguments"
            raise ValueError(msg)
        for line in frontmatter.splitlines():
            quoted = [m.span() for m in _QUOTED.finditer(line)]
            for match in _PLACEHOLDER.finditer(line):
                if not any(start < match.start() and match.end() < end for start, end in quoted):
                    msg = f"Template {self.name!r}: put {match.group(0)} in a double-quoted string, e.g. ref: "
                    msg += f'"{match.group(0)}"'
                    raise ValueError(msg)

    @classmethod
    def from_file(cls, path: Path) -> "ContractTemplate":
        """The template in `path`, named after the file: `weekly-report.prose.md` is `weekly-report`.

        Raises:
            ValueError: If the file isn't a `*.prose.md`, or its name or text is invalid.
            OSError: If it can't be read.
        """
        if not path.name.endswith(TEMPLATE_SUFFIX):
            msg = f"Template {path} must be a {TEMPLATE_SUFFIX} file"
            raise ValueError(msg)
        return cls(name=path.name.removesuffix(TEMPLATE_SUFFIX), text=path.read_text(encoding="utf-8"))

    def fill(self, assets: Mapping[str, str]) -> str:
        """The text with the placeholders of `assets` replaced by their references; the others stay as they are.

        One pass, so a reference that looks like a placeholder stays as it is. A reference is cleaned (`clean_text`)
        and inserted JSON-escaped into the double-quoted string its placeholder is in, which stays one string.
        """

        def replace(match: re.Match[str]) -> str:
            ref = assets.get(match.group(1))
            return match.group(0) if ref is None else json.dumps(clean_text(ref), ensure_ascii=False)[1:-1]

        return _PLACEHOLDER.sub(replace, self.text)

    def placeholders(self) -> list[str]:
        """The asset types its placeholders name, in order of first use."""
        return list(dict.fromkeys(_PLACEHOLDER.findall(self.text)))


def _split(text: str) -> tuple[str, str]:
    """The frontmatter between the leading `---` lines and the rest; no frontmatter, everything is body."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return "", text
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "".join(lines[1:index]), "".join(lines[index + 1 :])
    return "".join(lines[1:]), ""


def templates_in(paths: Iterable[Path | ContractTemplate]) -> tuple[ContractTemplate, ...]:
    """Templates from `ContractTemplate`s, template files and directories of `*.prose.md` files, sorted by name.

    Raises:
        ValueError: If a file is invalid or two templates share a name.
        OSError: If a file can't be read.
    """
    found: list[ContractTemplate] = []
    for item in paths:
        if isinstance(item, ContractTemplate):
            found.append(item)
        elif item.is_dir():
            found += [ContractTemplate.from_file(p) for p in sorted(item.glob(f"*{TEMPLATE_SUFFIX}")) if p.is_file()]
        else:
            found.append(ContractTemplate.from_file(item))
    names = [t.name for t in found]
    if duplicates := sorted({n for n in names if names.count(n) > 1}):
        msg = f"Two templates are named {duplicates[0]!r}"
        raise ValueError(msg)
    return tuple(sorted(found, key=lambda t: t.name))


@dataclass(frozen=True, kw_only=True, slots=True)
class PlaybookLet(Let):
    """What a module contributes for a kind of team work: asset types, template contracts, recommended settings.

    Named after its module, one per module. Data only: the `workflow` module evaluates hints through capability
    handles that only read, offers the templates, and applies a recommended setting only when the person accepts it.
    """

    summary: str
    """What the playbook is for, in one sentence."""
    assets: tuple[AssetType, ...] = ()
    templates: tuple[ContractTemplate, ...] = ()
    defaults: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    """Recommended settings by `<section>.<key>`, e.g. `{"capabilities.tracker": "github"}`; applied only on
    acceptance, through the same validation as `hz system config set`. Only capability bindings (never
    `agent_runner`) and the module's own settings: a playbook is data and can't recommend what runs code, what the
    agent may do, what it costs or where contracts come from."""

    def __post_init__(self) -> None:
        """Refuse an empty summary, duplicate asset types, setting keys that aren't keys and unknown placeholders.

        Raises:
            ValueError: If the playbook is malformed.
        """
        if not self.summary.strip():
            msg = f"Playbook {self.name!r} needs a summary"
            raise ValueError(msg)
        names = [a.name for a in self.assets]
        if duplicates := sorted({n for n in names if names.count(n) > 1}):
            msg = f"Playbook {self.name!r} has two asset types named {duplicates[0]!r}"
            raise ValueError(msg)
        if bad := [key for key in self.defaults if not SETTING.fullmatch(key)]:
            msg = f"Playbook {self.name!r}: {bad[0]!r} isn't a setting key such as capabilities.tracker"
            raise ValueError(msg)
        if refused := [key for key in self.defaults if not self._may_recommend(key)]:
            msg = (
                f"Playbook {self.name!r} can't recommend {refused[0]!r}: only capability bindings other than "
                f"agent_runner, and its own `{self.name}.*` settings"
            )
            raise ValueError(msg)
        templates = {t.name for t in self.templates}
        if kept := [a for a in self.assets if a.kept_by is not None and a.kept_by not in templates]:
            msg = f"Asset type {kept[0].name!r} of playbook {self.name!r} is kept by an unknown template "
            msg += repr(kept[0].kept_by)
            raise ValueError(msg)
        for template in self.templates:
            if unknown := [p for p in template.placeholders() if p not in names]:
                msg = f"Template {template.name!r} of playbook {self.name!r} names an unknown asset type {unknown[0]!r}"
                raise ValueError(msg)
        object.__setattr__(self, "assets", tuple(self.assets))
        object.__setattr__(self, "templates", tuple(self.templates))
        object.__setattr__(self, "defaults", MappingProxyType(dict(self.defaults)))

    def _may_recommend(self, key: str) -> bool:
        section, _, rest = key.partition(".")
        if section == "capabilities":
            capability = rest.replace("_", "-")
            return capability in CAPABILITIES and capability != "agent-runner"
        return section == self.name


class PlaybookSetup:
    """Runs a playbook's setup step: a service of the module that owns contracts (the built-in `workflow`).

    `Module.playbook` declares a setup step (ADR 0032) for its module that asks for this service by type, so
    `hz <module> setup` and `hz setup` walk through the playbook only when such a module is enabled.
    """

    def run(self, playbook: PlaybookLet, prompter: Prompter, editor: ConfigEditor) -> None:
        """Propose the playbook's settings, the assets its hints find and its templates; apply what is accepted.

        Raises:
            NotImplementedError: Always; the providing module implements it.
        """
        raise NotImplementedError
