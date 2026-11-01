"""`hz system update`: move the installation to the latest release or the newest commit, or say how to move it.

uv keeps a git requirement at the ref it was installed from: `uv tool upgrade` leaves `heinzel @ git+…@v0.2.0` at
v0.2.0 for good. So an installation pinned to a release tag is reinstalled from the tag of the highest release, with
every plugin as the receipt has it (ADR 0006). A branch, a commit or a local source could mean anything, so nothing is
guessed there: the result says what it is pinned to and which command moves it. An installation that follows a git
repository's default branch or the package index is upgraded by uv, which moves both; one pinned to `main`, the
default branch, is reinstalled from the same spec, which fetches its newest commit. No result suggests a release that
is older than the installed version.

Plugins are updated in the same `uv tool install` run as hz, so one resolver step keeps their dependencies consistent
with the new core. Each plugin is installed from the spec the receipt recorded (a PyPI requirement, or a git spec with
its `#subdirectory=`); a plugin from the core's own repository follows the core's ref, and a git branch or an index is
asked again, so `@main` moves to the newest commit. A plugin that blocks the install never blocks hz: it is kept at the
version it has if that still resolves, and otherwise taken out of the installation with the command that brings it back.
"""

import json
import re
import shlex
import tempfile
from collections.abc import Generator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from heinzel import HeinzelError, __version__
from heinzel.kernel import (
    CorePin,
    DaemonHandoverPlan,
    DaemonInstallationLifecycle,
    DaemonMutationScope,
    Installation,
    ReleaseSource,
    subprocess_environment,
)
from heinzel.modules.system._models import PluginUpdate, UpdateResult
from heinzel.modules.system._service import DEFAULT_REPOSITORY, RELEASE_TIMEOUT
from heinzel.modules.system.plugin._sources import git_credentials

DEFAULT_BRANCH = "main"
"""The repository's default branch: a pin to it is kept in place, the way an install without a ref is."""

type UpdateChannel = Literal["stable", "edge", "main", "release"]
"""Where updates come from; `release` is the earlier name of stable, still accepted (ADR 0114)."""

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
class Installed:
    """A plugin as a fresh `hz` reports it: its version and, for git, the commit."""

    version: str
    revision: str | None


class UpdateService:
    """Updates the uv tool environment Heinzelmännchen runs in, keeping its plugins."""

    def __init__(
        self,
        installation: Installation,
        releases: ReleaseSource,
        *,
        lifecycle: DaemonInstallationLifecycle | None = None,
        require_lifecycle: bool = False,
    ) -> None:
        """Create the service with an optional trusted lifecycle boundary."""
        self._installation = installation
        self._releases = releases
        self._lifecycle = lifecycle
        self._require_lifecycle = require_lifecycle

    def _mutation(self) -> AbstractContextManager[DaemonMutationScope | None]:
        if self._lifecycle is not None:
            return self._lifecycle.mutation(
                self._installation.startup_state_matches, self._installation.rollback_identity
            )
        if self._require_lifecycle:
            msg = "The daemon lifecycle boundary is unavailable; the installation was not changed."
            raise HeinzelError(msg)
        return nullcontext(None)

    def _require_running_version(self, current: str) -> None:
        try:
            installed = self._installation.installed_core_version()
            matched = Version(installed) == Version(current)
        except (HeinzelError, InvalidVersion) as exc:
            msg = "The installed core version cannot be verified; reload the application before updating."
            raise HeinzelError(msg) from exc
        if not matched:
            msg = (
                f"This process is running hz {current}, but the installation is hz {installed}; reload before updating."
            )
            raise HeinzelError(msg)

    def _automatic_generation(self, current: str) -> str | None:
        if self._lifecycle is None:
            self._require_running_version(current)
            return None
        before = self._installation.startup_fingerprint()
        self._require_running_version(current)
        if self._installation.startup_fingerprint() != before:
            msg = "The installation changed while checking its version; reload before updating."
            raise HeinzelError(msg)
        return before

    def _require_generation(self, current: str, initial: str | None) -> None:
        if initial is not None and self._installation.startup_fingerprint() != initial:
            msg = "The installation changed since this update was prepared; reload before updating."
            raise HeinzelError(msg)
        self._require_running_version(current)
        if initial is not None and self._installation.startup_fingerprint() != initial:
            msg = "The installation changed while checking its version; reload before updating."
            raise HeinzelError(msg)

    def _verify_automatic_generation(
        self, current: str, initial: str | None, scope: DaemonMutationScope | None
    ) -> None:
        try:
            self._require_generation(current, initial)
        except HeinzelError:
            if scope is not None:
                self._abort_or_leave_pending(scope)
            raise

    def _verify_selection(self, pin: CorePin, plugins: list[CorePin], scope: DaemonMutationScope | None) -> None:
        if self._installation.core_pin() != pin or self._installation.plugin_pins() != plugins:
            if scope is not None:
                self._abort_or_leave_pending(scope)
            msg = "The installation requirements changed before the update; review them and retry."
            raise HeinzelError(msg)
        try:
            names = [canonicalize_name(Requirement(plugin.requirement).name) for plugin in plugins]
        except InvalidRequirement as exc:
            if scope is not None:
                self._abort_or_leave_pending(scope)
            msg = "The installed plugin requirements are invalid; inspect them before updating."
            raise HeinzelError(msg) from exc
        if "heinzel" in names or len(set(names)) != len(names):
            if scope is not None:
                self._abort_or_leave_pending(scope)
            msg = "The installed tool requirements name the same distribution more than once; inspect them first."
            raise HeinzelError(msg)

    @staticmethod
    def _abort_or_leave_pending(scope: DaemonMutationScope) -> bool:
        try:
            scope.abort_unchanged()
        except (HeinzelError, OSError):
            return False
        return True

    def _finish_mutation(
        self, scope: DaemonMutationScope, plan: DaemonHandoverPlan, candidate: str, result: UpdateResult
    ) -> UpdateResult:
        if scope.checkpoint_matched:
            self._installation.record_startup_state(expected=candidate)
        scope.verified_complete(candidate)
        handover = scope.request(plan)
        if handover.requested:
            message = f"{result.message}\nThe daemon handover was requested; a fresh service tick is pending."
            return result.model_copy(update={"message": message})
        return result

    def update(
        self, source: str | None = None, current: str = __version__, channel: UpdateChannel = "stable"
    ) -> UpdateResult:
        """Update to the latest release, or reinstall from `source`; `current` is the version running now.

        `channel` is where the installation follows: `edge` (the newest edge release's wheel, verified by digest) or
        stable, which `release` also names: the releases (a pin to the newest release tag, which every later
        update follows; an installation on `main` moves there once a release is as new as it, never as a downgrade) or
        `main` (its newest commit).

        Raises:
            HeinzelError: If Heinzelmännchen wasn't installed with `uv tool install`, the latest release can't be
                asked for, or the install fails, naming the plugins that block it; Heinzelmännchen still works.
        """
        pin, requirements = self._installation.core_pin(), self._installation.requirements()
        if pin is None or not requirements:
            msg = "Heinzelmännchen wasn't installed with `uv tool install`; update it the way you installed it."
            raise HeinzelError(msg)
        plugins = self._installation.plugin_pins()
        if source is not None:
            return self._install(source, pin, plugins, current=current, latest=None, target=source, automatic=False)
        generation = self._automatic_generation(current)
        if channel == "main":
            result = self._install(
                self._at(pin, DEFAULT_BRANCH),
                pin,
                plugins,
                current=current,
                latest=None,
                target=None,
                channel="main",
                generation=generation,
            )
            return self._with_release_hint(pin, result)
        if channel == "edge":
            return self._to_edge(pin, plugins, current, generation)
        return self._to_stable(pin, plugins, current, generation)

    def _to_stable(self, pin: CorePin, plugins: list[CorePin], current: str, generation: str | None) -> UpdateResult:
        """Follow stable from wherever the installation is pinned."""
        if pin.is_edge:
            return self._from_edge(pin, plugins, current, generation)
        if pin.kind == "release":
            return self._to_latest_release(pin, plugins, current, generation)
        if pin.kind == "ref" and pin.ref == DEFAULT_BRANCH:
            return self._switch_to_releases(pin, plugins, current, generation)
        if pin.kind in ("default", "index"):
            return self._install(
                pin.requirement, pin, plugins, current=current, latest=None, target=None, generation=generation
            )
        return self._with_plugins(self._pinned(pin, current), pin, plugins, generation)

    @staticmethod
    def _at(pin: CorePin, ref: str) -> str:
        """The requirement at `ref`, of the default repository if the installation isn't from git."""
        if pin.git is not None:
            return pin.at(ref)
        return f"{pin.name} @ git+https://github.com/{DEFAULT_REPOSITORY}@{ref}"

    def _to_edge(self, pin: CorePin, plugins: list[CorePin], current: str, generation: str | None) -> UpdateResult:
        """Install the newest edge release, unless that would move back or the installation is pinned on purpose.

        Raises:
            HeinzelError: If the edge pointer can't be read.
        """
        if pin.kind in ("commit", "local", "url") and not pin.is_edge:
            return self._with_plugins(self._pinned(pin, current), pin, plugins, generation)
        try:
            edge = self._releases.edge_release(RELEASE_TIMEOUT)
        # Isolation boundary: whatever the package source raises, the user gets one error with what to do.
        except Exception as exc:
            msg = f"Could not ask for the newest edge release ({exc}); hz {current} stays as it is."
            raise HeinzelError(msg) from exc
        if Version(edge.version) <= _version(current):
            message = f"hz {current} is up to date (edge; the newest edge release is {edge.version})."
            same = UpdateResult(
                before=current,
                after=current,
                latest=edge.version,
                pinned_to=pin.ref,
                updated=False,
                message=message,
                next=[],
            )
            return self._with_plugins(same, pin, plugins, generation)
        return self._install(
            f"{pin.name} @ {edge.wheel}#sha256={edge.sha256}",
            pin,
            plugins,
            current=current,
            latest=edge.version,
            target=f"edge {edge.version}",
            channel="edge",
            generation=generation,
        )

    def _from_edge(self, pin: CorePin, plugins: list[CorePin], current: str, generation: str | None) -> UpdateResult:
        """Move to stable once it is at least as new; until then edge stays, because nothing ever downgrades.

        Raises:
            HeinzelError: If the highest stable release can't be asked for.
        """
        try:
            stable = self._releases.highest_version(RELEASE_TIMEOUT, prereleases=False)
        # Isolation boundary: whatever the package source raises, the user gets one error with what to do.
        except Exception as exc:
            msg = f"Could not ask for the newest stable release ({exc}); hz {current} stays as it is."
            raise HeinzelError(msg) from exc
        if Version(stable) < _version(current):
            message = (
                f"hz {current} (edge) is newer than stable {stable}, so it stays until stable catches up. Nothing is "
                "downgraded and your data is unchanged.\nTo follow edge again: hz system channel --to edge --yes"
            )
            ahead = UpdateResult(
                before=current, after=current, latest=stable, pinned_to=pin.ref, updated=False, message=message, next=[]
            )
            return self._with_plugins(ahead, pin, plugins, generation)
        return self._install(
            f"{pin.name}>={stable}",
            pin,
            plugins,
            current=current,
            latest=stable,
            target=f"stable {stable}",
            channel="stable",
            generation=generation,
        )

    def channel_summary(self, selected: UpdateChannel, current: str = __version__) -> str:
        """Where the next update goes on the chosen channel; asks the network and changes nothing."""
        chosen = "stable" if selected == "release" else selected
        return (
            f"You are on {chosen}. {self._channel_effect(chosen, current).replace('That update', 'The next update', 1)}"
        )

    def channel_statement(self, selected: UpdateChannel, target: UpdateChannel, current: str = __version__) -> str:
        """What choosing `target` changes, said before it happens; asks the network and changes nothing."""
        chosen = "stable" if target == "release" else target
        if chosen == ("stable" if selected == "release" else selected):
            return f"You are already on {chosen}, with hz {current}. Nothing changes."
        lines = [f"Moves from {selected} to {chosen}. Nothing is installed until you run hz system update."]
        lines.append(self._channel_effect(chosen, current))
        lines.append("Your settings, plugins and data stay. Switch again at any time; the choice survives updates.")
        return "\n".join(lines)

    def _channel_effect(self, chosen: str, current: str) -> str:
        try:
            if chosen == "edge":
                newest = self._releases.edge_release(RELEASE_TIMEOUT).version
                if Version(newest) <= _version(current):
                    return f"hz {current} stays: the newest edge release, {newest}, is not newer."
                return f"That update moves hz {current} to edge {newest}: every green change, sooner and less tested."
            if chosen == "main":
                return f"That update moves hz {current} to the newest commit of main."
            stable = self._releases.highest_version(RELEASE_TIMEOUT, prereleases=False)
        # Isolation boundary: not knowing the versions must not stop the choice; the statement says so.
        except Exception as exc:  # noqa: BLE001
            return f"The newest {chosen} version could not be asked for now ({exc})."
        if Version(stable) < _version(current):
            return (
                f"hz {current} is newer than stable {stable}, so it stays until stable catches up. It is never "
                "downgraded."
            )
        return f"That update moves hz {current} to stable {stable}, which has passed acceptance."

    def _switch_to_releases(
        self, pin: CorePin, plugins: list[CorePin], current: str, generation: str | None
    ) -> UpdateResult:
        """Reinstall from the newest release's tag, unless that is a downgrade.

        Raises:
            HeinzelError: If the latest release can't be asked for.
        """
        try:
            latest = self._latest(current)
        # Isolation boundary: whatever the package source raises, the user gets one error with what to do.
        except Exception as exc:
            msg = f"Could not ask for the latest release ({exc}); hz {current} stays at {pin.ref}."
            raise HeinzelError(msg) from exc
        if Version(latest) < _version(current):
            message = (
                f"hz {current} (main) is ahead of the latest release, {latest}; it moves to releases with the next "
                "release.\nTo stay on main: hz system update --channel main"
            )
            ahead = UpdateResult(
                before=current, after=current, latest=latest, pinned_to=pin.ref, updated=False, message=message, next=[]
            )
            return self._with_plugins(ahead, pin, plugins, generation)
        return self._install(
            self._at(pin, f"v{latest}"),
            pin,
            plugins,
            current=current,
            latest=latest,
            target=f"v{latest}",
            channel="releases",
            generation=generation,
        )

    def _to_latest_release(
        self, pin: CorePin, plugins: list[CorePin], current: str, generation: str | None
    ) -> UpdateResult:
        try:
            latest = self._latest(current)
        # Isolation boundary: whatever the package source raises, the user gets one error with what to do.
        except Exception as exc:
            source = shlex.quote(pin.source_at(PLACEHOLDER_TAG))
            msg = (
                f"Could not ask for the latest release ({exc}); hz {current} stays at {pin.ref}.\n"
                f"Try again later, or move to a release you choose: hz system update --source {source}"
            )
            raise HeinzelError(msg) from exc
        if Version(latest) <= _version(current):
            current_release = UpdateResult(
                before=current,
                after=current,
                latest=latest,
                pinned_to=pin.ref,
                updated=False,
                message=f"hz {current} is up to date (releases).",
                next=[],
            )
            return self._with_plugins(current_release, pin, plugins, generation)
        return self._install(
            pin.at(f"v{latest}"),
            pin,
            plugins,
            current=current,
            latest=latest,
            target=f"v{latest}",
            generation=generation,
        )

    def _release_hint(self, pin: CorePin, after: str) -> tuple[str | None, str | None]:
        """The newest release, and the command that moves to it, if it is newer than `after`; else None for both."""
        try:
            latest = self._latest(after)
        except Exception:  # noqa: BLE001 - isolation boundary: without the latest release there is nothing to suggest
            return None, None
        if Version(latest) <= _version(after):
            return latest, None
        release = (
            f"git+https://github.com/{DEFAULT_REPOSITORY}@v{latest}" if pin.git is None else pin.source_at(f"v{latest}")
        )
        return latest, f"hz system update --source {shlex.quote(release)}"

    def _with_release_hint(self, pin: CorePin, result: UpdateResult) -> UpdateResult:
        """`result` of an in-place update, with a line about a release newer than what is installed now."""
        latest, command = self._release_hint(pin, result.after)
        if command is None:
            return result.model_copy(update={"latest": latest})
        message = f"{result.message}\nA release newer than this build exists: run {command} to switch to releases."
        return result.model_copy(update={"latest": latest, "message": message, "next": [command]})

    def _pinned(self, pin: CorePin, current: str) -> UpdateResult:
        """Say what the installation is pinned to and which commands move it; change nothing."""
        latest, to_release = self._release_hint(pin, current)
        commands = [to_release] if to_release else []
        what = f"the latest release ({latest})" if latest else "a release"
        match pin.kind:
            case "commit":
                message = f"Pinned to commit {pin.ref}."
                if to_release:
                    message += f" To update to {what} run: {to_release}"
            case "ref":
                to_ref = f"hz system update --source {shlex.quote(pin.source_at(str(pin.ref)))}"
                commands.append(to_ref)
                message = f"Pinned to {pin.ref}; to get its newest commit run: {to_ref}"
                if to_release:
                    message += f"\nA release newer than this build exists: run {to_release} to switch to releases."
            case _:  # a local directory or file, or a URL
                message = f"Installed from {pin.ref}; to update, run hz system update --source with a newer build."
                if to_release:
                    message += f"\nA release newer than this build exists: run {to_release} to switch to releases."
        return UpdateResult(
            before=current,
            after=current,
            latest=latest,
            pinned_to=pin.ref,
            updated=False,
            message=message,
            next=commands,
        )

    def _install(  # noqa: PLR0913 - preserve one validated target for update and recovery
        self,
        core: str,
        pin: CorePin,
        plugins: list[CorePin],
        *,
        current: str,
        latest: str | None,
        target: str | None,
        channel: str | None = None,
        automatic: bool = True,
        generation: str | None = None,
    ) -> UpdateResult:
        with self._mutation() as scope:
            plan = scope.capture() if scope is not None else None
            with self._installation.tool_transaction():
                self._verify_selection(pin, plugins, scope)
                if automatic:
                    self._verify_automatic_generation(current, generation, scope)
                result, candidate = self._install_scoped(
                    core,
                    pin,
                    plugins,
                    current=current,
                    latest=latest,
                    target=target,
                    channel=channel,
                    scope=scope,
                    explicit_source=not automatic,
                )
                if scope is not None and plan is not None and candidate is not None:
                    result = self._finish_mutation(scope, plan, candidate, result)
                return result

    def _install_scoped(  # noqa: PLR0913 - what is installed, from where, with which plugins, and what for
        self,
        core: str,
        pin: CorePin,
        plugins: list[CorePin],
        *,
        current: str,
        latest: str | None,
        target: str | None,
        channel: str | None = None,
        scope: DaemonMutationScope | None = None,
        explicit_source: bool = False,
    ) -> tuple[UpdateResult, str | None]:
        """Install `core` with every plugin in one run; a plugin that blocks it is kept or taken out, never blocking.

        Raises:
            HeinzelError: If hz can't be installed even without its plugins, or doesn't start after it.
        """
        before = self._plugin_versions()
        activity = f"Updating Heinzelmännchen{f' to {target}' if target else ''}"
        environment = git_credentials(subprocess_environment())
        original = self._original_generation()
        with self._install_constraints(
            core, current, self._constraint_candidates(core, plugins, before), enabled=not explicit_source, scope=scope
        ) as (constraints, captured_constraints):
            modes, reasons, failure = self._converge(
                core, plugins, before, activity, environment=environment, may_drop=True, constraints=constraints
            )
        if modes is None:
            constraint_hint = (
                " Inspect existing constraints; to change them intentionally, choose an explicit uv command."
                if explicit_source and constraints is not None
                else ""
            )
            if scope is not None:
                unchanged = self._abort_or_leave_pending(scope)
                detail = (
                    "The original installation remains unchanged."
                    if unchanged
                    else "The installation is uncertain; explicit recovery is required."
                )
                msg = f"Could not update hz {current}: {failure}\n{detail}{constraint_hint}"
                raise HeinzelError(msg)
            failure_message = self._failure(
                pin, plugins, current, target, failure, environment=environment, original=original
            )
            msg = f"{failure_message}{constraint_hint}"
            raise HeinzelError(msg)
        candidate = self._installation.startup_fingerprint() if scope is not None else None
        selected = self._selected_requirements(core, plugins, modes, before)
        metadata = self._installation.verify_installed_requirements(
            selected,
            expected_version=target.removeprefix("v")
            if target and re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:[a-zA-Z0-9.+-]*)?", target)
            else None,
            forbidden=tuple(plugin.name for plugin in plugins if modes[canonicalize_name(plugin.name)] == "drop"),
            constraints=captured_constraints,
        )
        if not explicit_source and Version(metadata["heinzel"]) < Version(current):
            msg = (
                "The resolved automatic update is older than the installed core; "
                "no changed code was started and explicit recovery is required."
            )
            raise HeinzelError(msg)
        try:
            after = self._version()
        except HeinzelError as exc:
            installed = f"uv installed {target}" if target else "uv upgraded hz"
            recovery = (
                "The installation is uncertain; explicit recovery is required."
                if scope is not None
                else self._still_working(pin, plugins, current, environment, original)
            )
            msg = f"{installed}, but hz doesn't start.\n{recovery}"
            raise HeinzelError(msg) from exc
        if Version(after) != Version(metadata["heinzel"]):
            msg = "The running hz version does not match its inspected metadata."
            raise HeinzelError(msg)
        now = self._installation.core_pin()
        if target is None and after == current:  # nothing newer where the installation follows
            message = f"hz {current} is up to date{f' ({channel})' if channel else ''}."
        elif after == current:
            message = f"Reinstalled hz {current} from {target}"
        elif explicit_source:
            message = f"Installed hz {after} from {target}; this process is running hz {current}."
        else:
            message = f"Updated hz {current} → {after}."
        after_plugins = self._plugin_versions(strict=True)
        self._verify_plugin_versions(metadata, after_plugins)
        updates = self._plugin_updates(plugins, modes, reasons, before, after_plugins, core)
        message = _with_summary(message, updates)
        if candidate is not None and self._installation.startup_fingerprint() != candidate:
            msg = "The installation changed during post-update verification."
            raise HeinzelError(msg)
        return UpdateResult(
            before=current,
            after=after,
            latest=latest,
            pinned_to=now.ref if now else None,
            updated=after != current or target is not None or any(u.status == "upgraded" for u in updates),
            message=message,
            next=[],
            plugins=updates,
        ), candidate

    def _with_plugins(
        self, result: UpdateResult, pin: CorePin, plugins: list[CorePin], generation: str | None
    ) -> UpdateResult:
        """`result` of an update that leaves hz as it is, with the plugins asked again.

        The ones that follow a branch or an index may have a newer version. hz stays where it is, so a plugin that
        can't move is kept, never dropped.
        """
        # A core that follows a branch would move with the reinstall, which is `--source`'s job, not this one's.
        if pin.kind not in ("release", "commit", "local", "url") or not any(
            p.kind in ("index", "default", "ref") for p in plugins
        ):
            return result
        with self._mutation() as scope:
            plan = scope.capture() if scope is not None else None
            return self._refresh_plugins(result, pin, plugins, (scope, plan), generation)

    def _refresh_plugins(
        self,
        result: UpdateResult,
        pin: CorePin,
        plugins: list[CorePin],
        lifecycle: tuple[DaemonMutationScope | None, DaemonHandoverPlan | None],
        generation: str | None,
    ) -> UpdateResult:
        scope, plan = lifecycle
        environment = git_credentials(subprocess_environment())
        with self._installation.tool_transaction():
            self._verify_selection(pin, plugins, scope)
            self._verify_automatic_generation(result.before, generation, scope)
            before = self._plugin_versions()
            original = self._original_generation()
            candidates = self._constraint_candidates(pin.requirement, plugins, before)
            with self._install_constraints(pin.requirement, result.before, candidates, enabled=False, scope=scope) as (
                constraints,
                captured_constraints,
            ):
                modes, reasons, failure = self._converge(
                    pin.requirement,
                    plugins,
                    before,
                    "Updating the plugins",
                    environment=environment,
                    may_drop=False,
                    constraints=constraints,
                )
            if modes is None:
                if scope is not None:
                    unchanged = self._abort_or_leave_pending(scope)
                    if not unchanged:
                        msg = "The plugin update was uncertain; explicit recovery is required."
                        raise HeinzelError(msg)
                    recovery = "The original installation remains unchanged."
                else:
                    recovery = self._still_working(pin, plugins, result.before, environment, original)
                names = blocking_plugins([p.requirement for p in plugins], failure) or [
                    canonicalize_name(p.name) for p in plugins
                ]
                reason = f"Not updated: {_reason(failure)}"
                updates = [
                    self._unchanged(p, before, reason if canonicalize_name(p.name) in names else None) for p in plugins
                ]
                return result.model_copy(
                    update={"plugins": updates, "message": f"{_with_summary(result.message, updates)}\n{recovery}"}
                )
            candidate = self._installation.startup_fingerprint() if scope is not None else None
            selected = self._selected_requirements(pin.requirement, plugins, modes, before)
            metadata = self._installation.verify_installed_requirements(
                selected, expected_version=pin.version, constraints=captured_constraints
            )
            if Version(metadata["heinzel"]) < Version(result.before):
                msg = (
                    "The resolved automatic update is older than the installed core; "
                    "no changed code was started and explicit recovery is required."
                )
                raise HeinzelError(msg)
            if Version(self._version()) != Version(metadata["heinzel"]):
                msg = "The running hz version does not match its inspected metadata."
                raise HeinzelError(msg)
            after = self._plugin_versions(strict=True)
            self._verify_plugin_versions(metadata, after)
            updates = self._plugin_updates(plugins, modes, reasons, before, after, pin.requirement)
            refreshed = result.model_copy(
                update={
                    "plugins": updates,
                    "message": _with_summary(result.message, updates),
                    "updated": result.updated or any(u.status == "upgraded" for u in updates),
                }
            )
            if candidate is not None:
                if self._installation.startup_fingerprint() != candidate:
                    msg = "The installation changed during post-update verification."
                    raise HeinzelError(msg)
                if scope is not None and plan is not None:
                    refreshed = self._finish_mutation(scope, plan, candidate, refreshed)
            return refreshed

    def _selected_requirements(
        self, core: str, plugins: list[CorePin], modes: dict[str, Mode], before: dict[str, Installed]
    ) -> list[str]:
        return [
            self._installation.core_requirement(core),
            *(
                self._spec(
                    plugin, modes[canonicalize_name(plugin.name)], core, before.get(canonicalize_name(plugin.name))
                )
                for plugin in plugins
                if modes[canonicalize_name(plugin.name)] != "drop"
            ),
        ]

    @contextmanager
    def _install_constraints(
        self, core: str, current: str, candidates: list[str], *, enabled: bool, scope: DaemonMutationScope | None
    ) -> Generator[tuple[Path | None, tuple[str, ...]], None, None]:
        """Preserve prior constraints and add an automatic index floor where needed.

        Yields:
            A private constraints file when needed and the exact captured policy.

        Raises:
            HeinzelError: If existing constraints or the selected source cannot be preserved safely.
            OSError: If the private constraints file cannot be written or removed.
        """
        requirement = Requirement(self._installation.core_requirement(core))
        pinned = any(
            item.operator in {"==", "==="} and not item.version.endswith(".*") for item in requirement.specifier
        )
        try:
            constraints = self._installation.tool_constraints()
            if enabled and requirement.url is None and not pinned:
                constraints.append(f"heinzel>={Version(current)}")
            captured = tuple(constraints)
            # Candidates include new and keep alternatives for each plugin, not one simultaneous selection.
            for candidate in candidates:
                self._installation.validate_install_constraints([candidate], captured)
            if not constraints:
                yield None, captured
                return
            with tempfile.TemporaryDirectory(prefix="heinzel-update-") as directory:
                path = Path(directory) / "constraints.txt"
                path.write_text("\n".join(constraints) + "\n", encoding="utf-8")
                yield path, captured
        except (HeinzelError, OSError):
            if scope is not None:
                self._abort_or_leave_pending(scope)
            raise

    def _constraint_candidates(self, core: str, plugins: list[CorePin], before: dict[str, Installed]) -> list[str]:
        return [
            self._installation.core_requirement(core),
            *(
                self._spec(plugin, mode, core, before.get(canonicalize_name(plugin.name)))
                for plugin in plugins
                for mode in ("new", "keep")
            ),
        ]

    def _converge(  # noqa: PLR0913 - same credentials are used for the operation and recovery
        self,
        core: str,
        plugins: list[CorePin],
        before: dict[str, Installed],
        activity: str,
        *,
        environment: Mapping[str, str],
        may_drop: bool,
        constraints: Path | None = None,
    ) -> tuple[dict[str, Mode] | None, dict[str, str], str]:
        """Install `core` and every plugin; each plugin that blocks it is stepped down until the install works.

        A plugin goes in from its spec asked again (`new`); one uv names in a failed install is tried at the version it
        has (`keep`), and with `may_drop` left out (`drop`) if that doesn't resolve either. If uv names no one, every
        plugin is tried as it is. Returns the mode of each plugin, why those stepped down did, and the last failure
        (the modes are None if nothing worked).
        """
        names = [canonicalize_name(p.name) for p in plugins]
        modes: dict[str, Mode] = dict.fromkeys(names, "new")
        reasons: dict[str, str] = {}
        output = ""
        for _ in range(2 * len(plugins) + 2):
            specs = [
                self._spec(p, modes[n], core, before.get(n))
                for p, n in zip(plugins, names, strict=True)
                if modes[n] != "drop"
            ]
            command = self._installation.tool_install_command(core, specs, refresh=True)
            if constraints is not None:
                command.extend(["--constraints", str(constraints)])
            try:
                result = self._installation.attempt(command, activity, environment)
            except HeinzelError as exc:
                return None, reasons, str(exc)
            if result.returncode == 0:
                return modes, reasons, ""
            output = result.stderr.strip() or result.stdout.strip()
            if not _is_conflict(output):  # a network, credential or timeout failure is not the plugins' fault
                return None, reasons, output
            blocking = [n for n in blocking_plugins(specs, output) if n in modes and modes[n] != "drop"]
            suspects = (
                blocking
                or [n for n in names if modes[n] == "new"]
                or ([] if not may_drop else [n for n in names if modes[n] == "keep"])
            )
            if not suspects:
                break
            for name in suspects:
                if modes[name] == "keep" and not may_drop:
                    return None, reasons, output
                modes[name] = "keep" if modes[name] == "new" else "drop"
                reasons[name] = _reason(output)
        return None, reasons, output

    @staticmethod
    def _spec(plugin: CorePin, mode: Mode, _core: str, installed: Installed | None) -> str:
        """The requirement `plugin` goes in with."""
        if mode == "keep" and plugin.kind == "index" and installed is not None:
            return f"{plugin.name}=={installed.version}"
        if mode == "keep":
            if installed is not None and installed.revision and plugin.git:
                fragment = f"#subdirectory={plugin.subdirectory}" if plugin.subdirectory else ""
                return f"{plugin.name} @ git+{plugin.git}@{installed.revision}{fragment}"
            return plugin.requirement
        return plugin.requirement

    def _plugin_updates(
        self,
        plugins: list[CorePin],
        modes: dict[str, Mode],
        reasons: dict[str, str],
        before: dict[str, Installed],
        after: dict[str, Installed],
        _core: str,
    ) -> list[PluginUpdate]:
        """Report each plugin: upgraded, unchanged, or failed with what became of it."""
        updates = list[PluginUpdate]()
        for plugin in plugins:
            name = canonicalize_name(plugin.name)
            old, new = before.get(name), after.get(name)
            old_version, new_version = (old.version if old else None), (new.version if new else None)
            match modes[name]:
                case "drop":
                    handle = plugin.requirement
                    add = f"hz system plugin add {shlex.quote(handle)} --confirm"
                    reason = (
                        f"{reasons[name]} It does not allow the new hz, so it was taken out of the installation "
                        f"(disabled), and hz was updated without it. Once a release allows the new hz: {add}"
                    )
                    updates.append(
                        PluginUpdate(
                            distribution=name,
                            status="failed",
                            before=old_version,
                            after=None,
                            disabled=True,
                            reason=reason,
                            next=[add],
                        )
                    )
                case "keep":
                    reason = (
                        f"{reasons[name]} It stays at {old_version or 'its version'}, which still works with the new "
                        "hz; update it when its maintainers release a version that does."
                    )
                    updates.append(
                        PluginUpdate(
                            distribution=name, status="failed", before=old_version, after=new_version, reason=reason
                        )
                    )
                case _:
                    changed = (
                        old is not None
                        and new is not None
                        and (old.version, old.revision) != (new.version, new.revision)
                    )
                    updates.append(
                        PluginUpdate(
                            distribution=name,
                            status="upgraded" if changed else "unchanged",
                            before=old_version,
                            after=new_version,
                        )
                    )
        return updates

    @staticmethod
    def _unchanged(plugin: CorePin, before: dict[str, Installed], failure: str | None) -> PluginUpdate:
        name = canonicalize_name(plugin.name)
        version = before[name].version if name in before else None
        return PluginUpdate(
            distribution=name,
            status="failed" if failure else "unchanged",
            before=version,
            after=version,
            reason=failure,
        )

    def _plugin_versions(self, *, strict: bool = False) -> dict[str, Installed]:
        """Get plugin versions; ordinary pre-update reports can tolerate an unavailable runtime.

        Raises:
            HeinzelError: If strict post-update inspection cannot read the inventory.
        """
        try:
            items = json.loads(self._installation.hz("system", "plugin", "list", "--json"))
            return {
                canonicalize_name(str(i["distribution"])): Installed(str(i["version"]), i.get("revision"))
                for i in items
            }
        except (HeinzelError, ValueError, KeyError, TypeError) as exc:
            if strict:
                msg = "The updated plugin inventory could not be verified."
                raise HeinzelError(msg) from exc
            return {}

    @staticmethod
    def _verify_plugin_versions(metadata: dict[str, str], actual: dict[str, Installed]) -> None:
        for name, version in metadata.items():
            if name != "heinzel" and (name not in actual or Version(actual[name].version) != Version(version)):
                msg = "The updated plugin inventory does not match its inspected metadata."
                raise HeinzelError(msg)

    def _failure(  # noqa: PLR0913 - retain the exact environment for recovery
        self,
        pin: CorePin,
        plugins: list[CorePin],
        current: str,
        target: str | None,
        output: str,
        *,
        environment: Mapping[str, str],
        original: str | None,
    ) -> str:
        """What failed, which plugins are named in it, and that the previous hz still works or was put back."""
        to = f" to {target}" if target else ""
        blocking = blocking_plugins([p.requirement for p in plugins], output)
        named = (
            f" The plugin{'s' if len(blocking) > 1 else ''} {', '.join(blocking)} could not be kept either."
            if blocking
            else ""
        )
        headline = f"Could not update hz {current}{to}.{named}"
        recovery = self._still_working(pin, plugins, current, environment, original)
        return "\n".join(filter(None, [headline, recovery, output]))

    def _still_working(
        self, pin: CorePin, plugins: list[CorePin], current: str, environment: Mapping[str, str], original: str | None
    ) -> str:
        """Probe only the captured generation; otherwise try restoring the old receipt without executing it.

        uv replaces a tool environment only once the new one resolved and installed, so a failed `uv tool install`
        usually leaves the previous one in place. A restore is not proof that the old bytes were restored.
        """
        if self._same_generation(original):
            try:
                return f"hz {self._version()} is still installed, with every plugin."
            except HeinzelError:
                pass
        restore = self._installation.tool_install_command(pin.requirement, [p.requirement for p in plugins])
        try:
            restored = self._installation.attempt(restore, f"Restoring hz {current}", environment, recovery=True)
            if restored.returncode == 0 and self._same_generation(original):
                return f"hz {self._version()} was restored, with every plugin."
        except HeinzelError:
            pass
        return f"hz may not start; restore it with: {self._installation.tool_restore_hint(restore)}"

    def _original_generation(self) -> str | None:
        try:
            return self._installation.startup_fingerprint()
        except HeinzelError:
            return None

    def _same_generation(self, expected: str | None) -> bool:
        return expected is not None and self._original_generation() == expected

    def _latest(self, current: str) -> str:
        """The highest release, pre-releases included only when `current` is one."""
        return self._releases.highest_version(RELEASE_TIMEOUT, prereleases=_version(current).is_prerelease)

    def _version(self) -> str:
        """The version a fresh `hz` reports: the installation as it is now, not as this process loaded it.

        Raises:
            HeinzelError: If `hz` doesn't start.
        """
        return self._installation.hz("--version").splitlines()[0].removeprefix("hz ").strip()


def _with_summary(message: str, updates: list[PluginUpdate]) -> str:
    """`message` and a line for each plugin that was upgraded or failed; the unchanged ones are in the plugins."""
    lines: list[str] = []
    for update in updates:
        if update.status == "upgraded":
            lines.append(f"Plugin {update.distribution}: upgraded {update.before} → {update.after}.")
        elif update.status == "failed":
            lines.append(f"Plugin {update.distribution}: failed. {update.reason}")
    return "\n".join([message, *lines])


def _version(version: str) -> Version:
    """`version` parsed; a version that isn't one (a local build) counts as the lowest, so any release is newer."""
    try:
        return Version(version)
    except InvalidVersion:
        return Version("0")
