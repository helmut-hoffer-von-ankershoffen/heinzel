"""Playbooks: what a plugin contributes besides code, so the core stays generic (ADR 0037).

A playbook names the **asset types** a team keeps (a weekly report, a team tracker), how to find candidates for each
(**discovery hints**: capability reads the person can already make, narrowed by a title pattern), **template
contracts** that keep those assets true, and **recommended configuration**. It is data: the kernel checks its shape
and collects it; the `workflow` module evaluates the hints, offers the templates (`hz workflow new --template`) and
applies a recommended setting only when the person accepts it (`hz workflow setup`). Nothing in a playbook runs by
itself: hints only read, templates have no active schedule until the person adds one, and defaults are proposals.
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
from heinzel.kernel._duration import parse_duration
from heinzel.kernel._lets import Let
from heinzel.kernel._prompter import Prompter

SLUG = re.compile(r"^[a-z](?:-?[a-z0-9])*$")
"""Asset type and template names: lowercase words joined by single dashes, like contract names."""
READ = re.compile(r"^[a-z][a-z0-9-]*\.[a-z][a-z0-9_]*$")
SETTING = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$")
"""A recommended setting's key, as `hz system config set` takes it: `<section>.<key>`, e.g. `capabilities.tracker`."""
TEMPLATE_SUFFIX = ".prose.md"
PLACEHOLDER = "{{asset:%s}}"
"""How a template contract marks where a found asset's reference goes, e.g. `{{asset:weekly-report}}`."""
MAX_SLUG = 64


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
    """For methods that take a time window: how far back, e.g. `30d`."""
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
            parse_duration(self.window)
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
        """Refuse a bad name or an empty text.

        Raises:
            ValueError: If the name isn't a slug or the text is empty.
        """
        _slug("Template", self.name)
        if not self.text.strip():
            msg = f"Template {self.name!r} is empty"
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

        A reference is inserted JSON-escaped: a placeholder belongs in a double-quoted YAML string, which stays one
        string whatever the reference holds.
        """
        text = self.text
        for asset, ref in assets.items():
            text = text.replace(PLACEHOLDER % asset, json.dumps(ref, ensure_ascii=False)[1:-1])
        return text

    def placeholders(self) -> list[str]:
        """The asset types its placeholders name, in order of first use."""
        found = re.findall(r"\{\{asset:([a-z0-9-]+)\}\}", self.text)
        return list(dict.fromkeys(found))


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
    acceptance, through the same validation as `hz system config set`."""

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


class PlaybookSetup:
    """Runs a playbook's setup step: the service the module that consumes playbooks provides (`workflow` in the core).

    `Module.playbook` declares a setup step (ADR 0032) for its module that asks for this service by type, so
    `hz <module> setup` and `hz setup` walk through the playbook only when such a module is enabled.
    """

    def run(self, playbook: PlaybookLet, prompter: Prompter, editor: ConfigEditor) -> None:
        """Propose the playbook's settings, the assets its hints find and its templates; apply what is accepted.

        Raises:
            NotImplementedError: Always; the providing module implements it.
        """
        raise NotImplementedError
