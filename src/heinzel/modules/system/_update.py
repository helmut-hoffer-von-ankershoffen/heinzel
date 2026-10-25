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
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from heinzel import HeinzelError, __version__
from heinzel.kernel import CorePin, Installation, ReleaseSource, subprocess_environment
from heinzel.modules.system._models import PluginUpdate, UpdateResult
from heinzel.modules.system._service import DEFAULT_REPOSITORY, RELEASE_TIMEOUT
from heinzel.modules.system.plugin._sources import git_credentials

DEFAULT_BRANCH = "main"
"""The repository's default branch: a pin to it is kept in place, the way an install without a ref is."""

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
FIRST_PARTY_DIRECTORY = "plugins/heinzel-"


def _git_ref(spec: str) -> tuple[str | None, str | None]:
    """The repository and the ref a git requirement or `--source` installs from; None for anything that isn't git."""
    url = spec.split(" @ ", 1)[-1]
    if not url.startswith("git+"):
        return None, None
    parts = urlsplit(url.removeprefix("git+"))
    path, _, ref = parts.path.partition("@")
    return _repository(f"{parts.scheme}://{parts.netloc}{path}"), ref or None


def _repository(url: str) -> str:
    """A git repository URL as compared: no `.git` or trailing slash, lower case."""
    return url.removesuffix("/").removesuffix(".git").lower()


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

    def __init__(self, installation: Installation, releases: ReleaseSource) -> None:
        """Create the service; both are injected."""
        self._installation = installation
        self._releases = releases

    def update(
        self, source: str | None = None, current: str = __version__, channel: Literal["release", "main"] = "release"
    ) -> UpdateResult:
        """Update to the latest release, or reinstall from `source`; `current` is the version running now.

        `channel` is where the installation follows: the releases (a pin to the newest release tag, which every later
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
            return self._install(source, pin, plugins, current=current, latest=None, target=source)
        if channel == "main":
            result = self._install(
                self._at(pin, DEFAULT_BRANCH), pin, plugins, current=current, latest=None, target=None, channel="main"
            )
            return self._with_release_hint(pin, result)
        if pin.kind == "release":
            return self._to_latest_release(pin, plugins, current)
        if pin.kind == "ref" and pin.ref == DEFAULT_BRANCH:
            return self._switch_to_releases(pin, plugins, current)
        if pin.kind in ("default", "index"):
            return self._install(pin.requirement, pin, plugins, current=current, latest=None, target=None)
        return self._with_plugins(self._pinned(pin, current), pin, plugins)

    @staticmethod
    def _at(pin: CorePin, ref: str) -> str:
        """The requirement at `ref`, of the default repository if the installation isn't from git."""
        if pin.git is not None:
            return pin.at(ref)
        return f"{pin.name} @ git+https://github.com/{DEFAULT_REPOSITORY}@{ref}"

    def _switch_to_releases(self, pin: CorePin, plugins: list[CorePin], current: str) -> UpdateResult:
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
            return self._with_plugins(ahead, pin, plugins)
        return self._install(
            self._at(pin, f"v{latest}"),
            pin,
            plugins,
            current=current,
            latest=latest,
            target=f"v{latest}",
            channel="releases",
        )

    def _to_latest_release(self, pin: CorePin, plugins: list[CorePin], current: str) -> UpdateResult:
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
            return self._with_plugins(current_release, pin, plugins)
        return self._install(pin.at(f"v{latest}"), pin, plugins, current=current, latest=latest, target=f"v{latest}")

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

    def _install(  # noqa: PLR0913 - what is installed, from where, with which plugins, and what for
        self,
        core: str,
        pin: CorePin,
        plugins: list[CorePin],
        *,
        current: str,
        latest: str | None,
        target: str | None,
        channel: str | None = None,
    ) -> UpdateResult:
        """Install `core` with every plugin in one run; a plugin that blocks it is kept or taken out, never blocking.

        Raises:
            HeinzelError: If hz can't be installed even without its plugins, or doesn't start after it.
        """
        before = self._plugin_versions()
        activity = f"Updating Heinzelmännchen{f' to {target}' if target else ''}"
        modes, reasons, failure = self._converge(core, plugins, before, activity, may_drop=True)
        if modes is None:
            raise HeinzelError(self._failure(pin, plugins, current, target, failure))
        try:
            after = self._version()
        except HeinzelError as exc:
            installed = f"uv installed {target}" if target else "uv upgraded hz"
            msg = f"{installed}, but hz doesn't start.\n{self._still_working(pin, plugins, current)}"
            raise HeinzelError(msg) from exc
        now = self._installation.core_pin()
        if target is None and after == current:  # nothing newer where the installation follows
            message = f"hz {current} is up to date{f' ({channel})' if channel else ''}."
        elif after == current:
            message = f"Reinstalled hz {current} from {target}"
        else:
            message = f"Updated hz {current} → {after}."
        updates = self._plugin_updates(plugins, modes, reasons, before, self._plugin_versions(), core)
        message = _with_summary(message, updates)
        if after != current or target is not None:
            message = self._restart_daemon(message)
        return UpdateResult(
            before=current,
            after=after,
            latest=latest,
            pinned_to=now.ref if now else None,
            updated=after != current or target is not None or any(u.status == "upgraded" for u in updates),
            message=message,
            next=[],
            plugins=updates,
        )

    def _restart_daemon(self, message: str) -> str:
        """`message` and a line on the daemon: restarted with the new hz, or how to do it if that failed.

        An installed daemon keeps running the hz it was started with; the new hz restarts it, so the versions match.
        Nothing is said when no daemon is installed.
        """
        try:
            restarted = json.loads(self._installation.hz("system", "daemon", "restart", "--json")).get("restarted")
        except (HeinzelError, ValueError, AttributeError):
            return f"{message}\nThe daemon could not be restarted; run: hz system daemon restart"
        return f"{message}\nThe daemon was restarted with the new hz." if restarted else message

    def _with_plugins(self, result: UpdateResult, pin: CorePin, plugins: list[CorePin]) -> UpdateResult:
        """`result` of an update that leaves hz as it is, with the plugins asked again.

        The ones that follow a branch or an index may have a newer version. hz stays where it is, so a plugin that
        can't move is kept, never dropped.
        """
        # A core that follows a branch would move with the reinstall, which is `--source`'s job, not this one's.
        if pin.kind not in ("release", "commit", "local", "url") or not any(
            p.kind in ("index", "default", "ref") for p in plugins
        ):
            return result
        before = self._plugin_versions()
        modes, reasons, failure = self._converge(
            pin.requirement, plugins, before, "Updating the plugins", may_drop=False
        )
        if modes is None:  # nothing changed: uv replaces an environment only once the new one is installed
            names = blocking_plugins([p.requirement for p in plugins], failure) or [
                canonicalize_name(p.name) for p in plugins
            ]
            reason = f"Not updated: {_reason(failure)}"
            updates = [
                self._unchanged(p, before, reason if canonicalize_name(p.name) in names else None) for p in plugins
            ]
            return result.model_copy(update={"plugins": updates, "message": _with_summary(result.message, updates)})
        updates = self._plugin_updates(plugins, modes, reasons, before, self._plugin_versions(), pin.requirement)
        return result.model_copy(
            update={
                "plugins": updates,
                "message": _with_summary(result.message, updates),
                "updated": result.updated or any(u.status == "upgraded" for u in updates),
            }
        )

    def _converge(
        self, core: str, plugins: list[CorePin], before: dict[str, Installed], activity: str, *, may_drop: bool
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
        environment = git_credentials(subprocess_environment())
        output = ""
        for _ in range(2 * len(plugins) + 2):
            specs = [
                self._spec(p, modes[n], core, before.get(n))
                for p, n in zip(plugins, names, strict=True)
                if modes[n] != "drop"
            ]
            result = self._installation.attempt(
                self._installation.tool_install_command(core, specs, refresh=True), activity, environment
            )
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
    def _spec(plugin: CorePin, mode: Mode, core: str, installed: Installed | None) -> str:
        """The requirement `plugin` goes in with."""
        if mode == "keep" and plugin.kind == "index" and installed is not None:
            return f"{plugin.name}=={installed.version}"
        if mode == "keep":
            if installed is not None and installed.revision and plugin.git:
                fragment = f"#subdirectory={plugin.subdirectory}" if plugin.subdirectory else ""
                return f"{plugin.name} @ git+{plugin.git}@{installed.revision}{fragment}"
            return plugin.requirement
        repository, ref = _git_ref(core)
        if ref and plugin.git and repository == _repository(plugin.git) and plugin.subdirectory:
            return plugin.at(ref)  # a plugin of the core's own repository moves with the core
        return plugin.requirement

    def _plugin_updates(  # noqa: PLR0913, PLR0917 - the plan, why, and the states before and after
        self,
        plugins: list[CorePin],
        modes: dict[str, Mode],
        reasons: dict[str, str],
        before: dict[str, Installed],
        after: dict[str, Installed],
        core: str,
    ) -> list[PluginUpdate]:
        """Report each plugin: upgraded, unchanged, or failed with what became of it."""
        repository, _ = _git_ref(core)
        updates = list[PluginUpdate]()
        for plugin in plugins:
            name = canonicalize_name(plugin.name)
            old, new = before.get(name), after.get(name)
            old_version, new_version = (old.version if old else None), (new.version if new else None)
            match modes[name]:
                case "drop":
                    short = canonicalize_name(plugin.name).removeprefix("heinzel-")
                    own = (
                        plugin.git
                        and repository == _repository(plugin.git)
                        and (plugin.subdirectory or "").startswith(FIRST_PARTY_DIRECTORY)
                    )
                    handle = short if own else plugin.requirement
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

    def _plugin_versions(self) -> dict[str, Installed]:
        """The plugins a fresh `hz` has, by distribution; empty if it can't be asked (a report must not fail)."""
        try:
            items = json.loads(self._installation.hz("system", "plugin", "list", "--json"))
            return {
                canonicalize_name(str(i["distribution"])): Installed(str(i["version"]), i.get("revision"))
                for i in items
            }
        except (HeinzelError, ValueError, KeyError, TypeError):
            return {}

    def _failure(self, pin: CorePin, plugins: list[CorePin], current: str, target: str | None, output: str) -> str:
        """What failed, which plugins are named in it, and that the previous hz still works or was put back."""
        to = f" to {target}" if target else ""
        blocking = blocking_plugins([p.requirement for p in plugins], output)
        named = (
            f" The plugin{'s' if len(blocking) > 1 else ''} {', '.join(blocking)} could not be kept either."
            if blocking
            else ""
        )
        headline = f"Could not update hz {current}{to}.{named}"
        return "\n".join(filter(None, [headline, self._still_working(pin, plugins, current), output]))

    def _still_working(self, pin: CorePin, plugins: list[CorePin], current: str) -> str:
        """Check that hz still starts; if it doesn't, reinstall what the receipt had before.

        uv replaces a tool environment only once the new one resolved and installed, so a failed `uv tool install`
        leaves the previous one in place; this is the safety net if it ever doesn't.
        """
        try:
            return f"hz {self._version()} is still installed, with every plugin."
        except HeinzelError:
            pass
        restore = self._installation.tool_install_command(pin.requirement, [p.requirement for p in plugins])
        restored = self._installation.attempt(restore, f"Restoring hz {current}")
        if restored.returncode == 0:
            try:
                return f"hz {self._version()} was restored, with every plugin."
            except HeinzelError:
                pass
        return f"hz may not start; restore it with: {shlex.join(restore)}"

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
