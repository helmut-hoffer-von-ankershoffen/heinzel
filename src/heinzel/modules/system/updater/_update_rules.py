"""The update's pure rules: core and plugin plans, pins, the agreed moves and the messages."""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from heinzel import HeinzelError
from heinzel.kernel import (
    CorePin,
    HeadRefresh,
)
from heinzel.kernel._logging import get_logger
from heinzel.modules.system._models import AnnouncedUpdate, PluginPlan, PluginUpdate, UpdatePlan
from heinzel.modules.system.updater._announced import (
    Announcement,
    installed_identity,
)
from heinzel.modules.system.updater._phases import State
from heinzel.modules.system.updater._plugin_releases import PinnedPlugins
from heinzel.modules.system.updater._plugin_standing import standing

log = get_logger("update")
DEFAULT_BRANCH = "main"
"""The repository's default branch: a pin to it is kept in place, the way an install without a ref is."""

type UpdateChannel = Literal["stable", "edge", "main", "release"]
"""Where updates come from; `release` is the earlier name of stable, still accepted (ADR 0114)."""

DAEMON_SECONDS = 90.0
"""How long an update waits for the handed-over daemon's first tick to report the version it loaded."""
DAEMON_POLL_SECONDS = 1.0
DESKTOP_RECEIPT = Path(".config/heinzel/desktop-installation.json")
DEFERRED = 75
"""The exit code of `hz app install --restart` when the app was still open after `QUIT_SECONDS` (EX_TEMPFAIL)."""
QUIT_SECONDS = 20.0
"""How long `hz app install --restart` waits for an open app to quit (`heinzel.heads._desktop_installation`)."""
DESKTOP_SECONDS = 900
"""How long the new hz's `hz app install` may take: one verified download and a replace, as the installer allows."""
"""Under the home folder: the managed Mac app's receipt (`heinzel.heads._desktop_installation`)."""
SETTLED = frozenset({"verified", "not-installed", "self"})
"""Head outcomes that leave nothing running older code; `pending` and `failed` do not."""

PLACEHOLDER_TAG = "vX.Y.Z"
"""Stands in for the latest release tag in a suggested command when the package source couldn't be asked."""


def blocking_plugins(plugins: Sequence[str], output: str) -> list[str]:
    """The plugins, by distribution, that uv names in a failed install's `output`: the ones that block it."""
    text, names = output.lower(), list[str]()
    for plugin in plugins:
        try:
            name = canonicalize_name(Requirement(plugin).name)
        except InvalidRequirement:
            continue
        if re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", text) and name not in names:
            names.append(name)
    return names


type Mode = Literal["new", "keep", "drop"]
"""How a plugin goes into the install: from its spec asked again (`new`), at the version it has (`keep`) or not at all
(`drop`)."""

REASON_LENGTH = 300


def _release_version(target: str | None) -> str | None:
    """The version a release tag `vX.Y.Z` names; None for any other source."""
    if target and re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.+-]*", target):
        return target.removeprefix("v")
    return None


def _is_conflict(output: str) -> bool:
    """Whether uv's output says the requirements can't be resolved together, as opposed to a failure to fetch."""
    return bool(re.search(r"no solution found|unsatisfiable|depends on|conflict", output, re.IGNORECASE))


def _reason(output: str) -> str:
    """The line of uv's output that says why, shortened: the first one that names a cause, else the last one."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    line = next(
        (
            x
            for pattern in (r"because|depends on|requires|conflict", r"error")
            for x in lines
            if re.search(pattern, x, re.IGNORECASE)
        ),
        lines[-1] if lines else "",
    )
    return line[:REASON_LENGTH] or "uv failed without saying why"


@dataclass(frozen=True, slots=True)
class _Before:
    """The tool before an automatic update: its generation, and for a move to another interpreter its content."""

    generation: str
    content: str | None = None
    """The fingerprint without the folder's own identity: uv recreates the folder for another interpreter."""
    matched: bool = False
    """Whether the continuity record matched before; only then is it renewed after a put-back."""


_ORIGINALS: ContextVar[Mapping[str, _Before]] = ContextVar("update_originals", default=MappingProxyType({}))
"""Each generation captured before a move to another interpreter, with what a restore of it must match."""


@dataclass(frozen=True, slots=True)
class Installed:
    """A plugin as a fresh `hz` reports it: its version and, for git, the commit."""

    version: str
    revision: str | None


HEAD_LABELS = {"daemon": "Daemon", "web": "Web server", "notifier": "Notifier", "desktop": "Desktop app"}


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _pinned(plugin: CorePin) -> str | None:
    """What `plugin`'s install spec pins it to, so an update reinstalls the same; None if it follows newer ones."""
    ref = plugin.ref or ""
    if plugin.kind in {"release", "commit", "url", "local"} or (plugin.kind == "index" and ref.startswith("==")):
        return ref or plugin.requirement
    return None


@dataclass(frozen=True)
class _Agreed:
    """What one `update` call was allowed to move: nothing, unless a person agreed to a plan."""

    moves: Mapping[str, str] = field(default_factory=dict[str, str])
    """Each pinned plugin that moves, by canonical name, mapped to the requirement it goes in with."""
    asked: frozenset[str] = frozenset()
    """The pinned plugins whose releases the plan asked and found nothing newer or moved: none is "not checked"."""

    @classmethod
    def of(cls, plan: UpdatePlan | None) -> "_Agreed":
        """The moves of `plan`; nothing without one."""
        if plan is None:
            return cls()
        return cls(
            moves={p.distribution: p.moves_to for p in plan.plugins if p.moves_to},
            asked=frozenset(p.distribution for p in plan.plugins if p.action in {"update", "current"}),
        )


_AGREED: ContextVar[_Agreed] = ContextVar("heinzel_update_agreed", default=_Agreed())  # noqa: B039 - frozen, never changed
"""The agreement of the `update` call running in this thread or task; never shared between requests."""


def _verify_core_selection(selected: str, current: str, minimum: str | None, *, explicit_source: bool) -> None:
    """Check the resolved version before any changed Core code is started.

    Raises:
        HeinzelError: If uv selected an older version than the update allows.
    """
    if minimum is not None and Version(selected) < Version(minimum):
        msg = f"uv selected hz {selected} below the requested release {minimum}. No changed code was started."
        raise HeinzelError(msg)
    if not explicit_source and Version(selected) < Version(current):
        msg = (
            "The resolved automatic update is older than the installed core; "
            "no changed code was started and explicit recovery is required."
        )
        raise HeinzelError(msg)


def _install_message(  # noqa: PLR0913 - report the resolved version and how the source was chosen
    current: str,
    after: str,
    latest: str | None,
    *,
    target: str | None,
    channel: str | None,
    explicit_source: bool,
    index_lookup_failed: bool,
) -> str:
    if after == current:
        if target is not None:
            return f"Reinstalled hz {current} from {target}"
        if index_lookup_failed:
            return f"hz {current} stays installed. The newest stable release could not be checked."
        if latest is not None and Version(latest) > _version(current):
            return (
                f"hz {current} stays installed. The newest stable release is {latest}. uv kept the existing selection."
            )
        return f"hz {current} is up to date{f' ({channel})' if channel else ''}."
    if explicit_source:
        return f"Installed hz {after} from {target}; this process is running hz {current}."
    return f"Updated hz {current} → {after}"


def _index_preferred_release(
    pin: CorePin, current: str, latest: str | None, constraints: tuple[str, ...]
) -> str | None:
    """Prefer the known newest stable release only for an unpinned index installation."""
    if pin.kind != "index" or pin.ref is not None or latest is None or Version(latest) <= _version(current):
        return None
    for constraint in map(Requirement, constraints):
        if canonicalize_name(constraint.name) == "heinzel" and (
            constraint.url is not None or not constraint.specifier.contains(latest)
        ):
            return None
    return latest


def _core_line(current: str, latest: str | None, channel: str) -> str:
    """What happens to hz itself, in one line of the plan."""
    if channel in {"edge", "main"}:
        return f"hz {current} follows {channel}: its newest build is installed."
    if latest is None:
        return f"hz {current}: the newest release couldn't be asked; the update asks again."
    if _version(latest) > _version(current):
        return f"hz {current} → {latest}"
    return f"hz {current} is the newest release."


def _plan_token(latest: str | None, plans: Sequence[PluginPlan]) -> str:
    """A digest of what the plan would install: hz's newest release and each plugin's target and requirement."""
    facts = [latest, *([p.distribution, p.action, p.target, p.moves_to] for p in plans)]
    return hashlib.sha256(json.dumps(facts, separators=(",", ":")).encode()).hexdigest()[:32]


def _pinned_plan(pin: CorePin, name: str, version: str | None, found: PinnedPlugins) -> PluginPlan | None:
    """The plan of a plugin whose pinned releases were asked; None if it wasn't among them."""
    pinned = _pinned(pin)
    short = pinned[:12] if pinned and pin.kind == "commit" else pinned
    index = pin.kind == "index"
    if (release := found.newer.get(name)) is not None:
        moves_to = f"{pin.name}>={release.version}" if index else _at_commit(pin, release.commit)
        how = "then it follows the package index" if index else "to the commit of its release tag"
        detail = f"Pinned to {short}; moves to release {release.version}, {how}."
        return PluginPlan(
            distribution=name,
            installed=version,
            target=release.version if moves_to else None,
            action="update" if moves_to else "kept",
            detail=detail if moves_to else f"Pinned to {short}; its release tag names no commit, so it stays.",
            moves_to=moves_to,
        )
    if name in found.unasked:
        detail = f"Pinned to {short}; stays there: {found.unasked[name]}."
        return PluginPlan(distribution=name, installed=version, action="kept", detail=detail)
    if name in found.ahead:
        detail = (
            f"Pinned to {short}, newer than its newest stable release {found.ahead[name]}; an update never moves "
            "back, so it stays."
        )
        return PluginPlan(distribution=name, installed=version, action="kept", detail=detail)
    if name in found.current:
        follows = index and version is not None
        return PluginPlan(
            distribution=name,
            installed=version,
            action="current",
            detail="It follows the package index from now on." if follows else "",
            moves_to=f"{pin.name}>={version}" if follows else None,  # a floor: nothing can resolve below it
        )
    return None


def _at_commit(pin: CorePin, commit: str | None) -> str | None:
    return pin.at(commit) if commit is not None and pin.git is not None else None


def _plugin_plan(
    pin: CorePin, installed: Mapping[str, str], found: PinnedPlugins, newer: Mapping[str, str]
) -> PluginPlan:
    """What a confirmed update would do to one plugin."""
    name = str(canonicalize_name(Requirement(pin.requirement).name))
    version = installed.get(name)
    if (asked := _pinned_plan(pin, name, version, found)) is not None:
        return asked
    pinned = _pinned(pin)
    if pinned is not None:
        short = pinned[:12] if pin.kind == "commit" else pinned
        detail = f"Pinned to {short}, which names no release to follow; an update leaves it as it is."
        return PluginPlan(distribution=name, installed=version, action="kept", detail=detail)
    if name in newer:
        return PluginPlan(distribution=name, installed=version, target=newer[name], action="update")
    if pin.kind == "index":
        return PluginPlan(distribution=name, installed=version, action="current")
    detail = "Follows its branch; the update asks for its newest commit."
    return PluginPlan(distribution=name, installed=version, action="refresh", detail=detail)


def _plugin_step(update: PluginUpdate) -> tuple[State, str]:
    """How one plugin's step ended: updated, already new, held back at its version or taken out."""
    if update.status == "upgraded":
        return "done", f"Updated {update.before or '?'} → {update.after or '?'}"
    if update.status == "unchanged":
        return ("done" if update.checked else "warn"), _sentence(standing(update))
    if update.disabled:
        return "warn", "Taken out: it does not allow this hz yet"
    return "warn", f"Held back at {update.before or 'its version'}: it does not allow the new hz yet"


def _with_heads(message: str, heads: list[HeadRefresh]) -> str:
    """`message`, with one line per installed head and, unless every one is settled, that the update isn't done."""
    shown = [head for head in heads if head.status != "not-installed"]
    if not shown:
        return message
    lines = [f"{HEAD_LABELS[head.head]}: {head.status}. {head.detail}".rstrip() for head in shown]
    if any(head.status not in SETTLED for head in shown):
        lines.append("Not everything runs the new version yet; the lines above say what finishes it.")
    return "\n".join([message, *lines])


def _with_summary(message: str, updates: list[PluginUpdate]) -> str:
    """`message` and a line for each plugin that was upgraded or failed; the unchanged ones are in the plugins."""
    lines: list[str] = []
    for update in updates:
        if update.status == "upgraded":
            lines.append(f"Plugin {update.distribution}: upgraded {update.before} → {update.after}.")
        elif update.status == "failed":
            lines.append(f"Plugin {update.distribution}: failed. {update.reason}")
    return "\n".join([message, *lines])


@dataclass(frozen=True)
class _Selection:
    """What `apply_announced` hands uv, decided before anything changes."""

    pin: CorePin
    core: str
    plugins: list[CorePin]
    names: list[str]
    specs: list[str]
    """Every plugin's requirement: as the receipt has it, or a plugin from git at its installed commit."""
    by_hand: list[str]
    """Plugins from git, a folder or a URL: never refreshed or reinstalled, unchanged afterwards."""
    held_at_commit: list[str]
    """Plugins from git held at their installed commit while installing; the receipt names their ref again after."""


def _by_hand(version: str, why: str) -> AnnouncedUpdate:
    """The one-line answer for an update that is made by hand; nothing was installed."""
    return AnnouncedUpdate(
        updated=False, after=version, restored=False, refused=True, message=f"{why}; hz isn't updated by itself."
    )


def _require_untouched(prefix: Path, untouched: Mapping[str, object]) -> None:
    """Every plugin updated by hand must be exactly as before: same version, same files, same source.

    Raises:
        HeinzelError: If one changed.
    """
    if changed := [name for name, was in untouched.items() if installed_identity(prefix, name) != was]:
        msg = f"the plugins updated by hand would have changed: {', '.join(changed)}"
        raise HeinzelError(msg)


def _added_options(announced: Announcement) -> dict[str, object]:
    """The `[tool.options]` uv records for the resolver options an automatic update passes, as uv writes them."""
    options: dict[str, object] = {}
    if announced.exclude_newer is not None:
        options["exclude-newer"] = _stamp(announced.exclude_newer)
    if announced.exclude_newer_core is not None:
        options["exclude-newer-package"] = {"heinzel": _stamp(announced.exclude_newer_core)}
    return options


def _stamp(moment: datetime) -> str:
    """A time as uv's `--exclude-newer` takes it: RFC 3339 in UTC."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _version(version: str) -> Version:
    """`version` parsed; a version that isn't one (a local build) counts as the lowest, so any release is newer."""
    try:
        return Version(version)
    except InvalidVersion:
        return Version("0")
