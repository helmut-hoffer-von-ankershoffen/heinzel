"""`hz system update`: move the installation to the latest release or the newest commit, or say how to move it.

uv keeps a git requirement at the ref it was installed from: `uv tool upgrade` leaves `heinzel @ git+…@v0.2.0` at
v0.2.0 for good. So an installation pinned to a release tag is reinstalled from the tag of the highest release, with
every plugin as the receipt has it (ADR 0006). A branch, a commit or a local source could mean anything, so nothing is
guessed there: the result says what it is pinned to and which command moves it. An installation that follows a git
repository's default branch or the package index is upgraded by uv, which moves both; one pinned to `main`, the
default branch, is reinstalled from the same spec, which fetches its newest commit. No result suggests a release that
is older than the installed version.
"""

import re
import shlex
from collections.abc import Sequence
from typing import Literal

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from heinzel import HeinzelError, __version__
from heinzel.kernel import CorePin, Installation, ReleaseSource
from heinzel.modules.system._models import UpdateResult
from heinzel.modules.system._service import DEFAULT_REPOSITORY, RELEASE_TIMEOUT

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
        plugins = requirements[1:]
        if source is not None:
            command = self._installation.tool_install_command(source, plugins)
            return self._install(command, pin, plugins, current=current, latest=None, target=source)
        if channel == "main":
            command = self._installation.tool_install_command(self._at(pin, DEFAULT_BRANCH), plugins)
            result = self._install(command, pin, plugins, current=current, latest=None, target=None, channel="main")
            return self._with_release_hint(pin, result)
        if pin.kind == "release":
            return self._to_latest_release(pin, plugins, current)
        if pin.kind == "ref" and pin.ref == DEFAULT_BRANCH:
            return self._switch_to_releases(pin, plugins, current)
        if pin.kind in ("default", "index"):
            command = self._installation.upgrade_command()
            return self._install(command, pin, plugins, current=current, latest=None, target=None)
        return self._pinned(pin, current)

    @staticmethod
    def _at(pin: CorePin, ref: str) -> str:
        """The requirement at `ref`, of the default repository if the installation isn't from git."""
        if pin.git is not None:
            return pin.at(ref)
        return f"{pin.name} @ git+https://github.com/{DEFAULT_REPOSITORY}@{ref}"

    def _switch_to_releases(self, pin: CorePin, plugins: list[str], current: str) -> UpdateResult:
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
            return UpdateResult(
                before=current, after=current, latest=latest, pinned_to=pin.ref, updated=False, message=message, next=[]
            )
        command = self._installation.tool_install_command(self._at(pin, f"v{latest}"), plugins)
        return self._install(
            command, pin, plugins, current=current, latest=latest, target=f"v{latest}", channel="releases"
        )

    def _to_latest_release(self, pin: CorePin, plugins: list[str], current: str) -> UpdateResult:
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
            return UpdateResult(
                before=current,
                after=current,
                latest=latest,
                pinned_to=pin.ref,
                updated=False,
                message=f"hz {current} is up to date (releases).",
                next=[],
            )
        command = self._installation.tool_install_command(pin.at(f"v{latest}"), plugins)
        return self._install(command, pin, plugins, current=current, latest=latest, target=f"v{latest}")

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

    def _install(  # noqa: PLR0913 - what is run, from where, with which plugins, and what for
        self,
        command: list[str],
        pin: CorePin,
        plugins: list[str],
        *,
        current: str,
        latest: str | None,
        target: str | None,
        channel: str | None = None,
    ) -> UpdateResult:
        """Run `command`; on failure name the plugins that block it and make sure the previous hz still works.

        Raises:
            HeinzelError: If `command` fails, or `hz` doesn't start after it.
        """
        result = self._installation.attempt(command, f"Updating Heinzelmännchen{f' to {target}' if target else ''}")
        if result.returncode != 0:
            output = result.stderr.strip() or result.stdout.strip()
            raise HeinzelError(self._failure(pin, plugins, current, target, output))
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
        return UpdateResult(
            before=current,
            after=after,
            latest=latest,
            pinned_to=now.ref if now else None,
            updated=after != current or target is not None,
            message=message,
            next=[],
        )

    def _failure(self, pin: CorePin, plugins: list[str], current: str, target: str | None, output: str) -> str:
        """What failed, which plugins block it, and that the previous hz still works or was put back."""
        blocking = blocking_plugins(plugins, output)
        to = f" to {target}" if target else ""
        if blocking:
            names = ", ".join(blocking)
            headline = (
                f"Could not update hz {current}{to}: the plugin {names} does not allow it."
                if len(blocking) == 1
                else f"Could not update hz {current}{to}: the plugins {names} do not allow it."
            )
            remove = "; ".join(f"hz system plugin remove {name}" for name in blocking)
            them = "it" if len(blocking) == 1 else "them"
            fix = (
                f"Remove {them} ({remove}), run hz system update, then add a release of {them} that allows the new "
                "hz (hz system plugin add <spec> --confirm)."
            )
        else:
            headline, fix = f"Could not update hz {current}{to}.", ""
        return "\n".join(filter(None, [headline, self._still_working(pin, plugins, current), fix, output]))

    def _still_working(self, pin: CorePin, plugins: list[str], current: str) -> str:
        """Check that hz still starts; if it doesn't, reinstall what the receipt had before.

        uv replaces a tool environment only once the new one resolved and installed, so a failed `uv tool install`
        leaves the previous one in place; this is the safety net if it ever doesn't.
        """
        try:
            return f"hz {self._version()} is still installed, with every plugin."
        except HeinzelError:
            pass
        restore = self._installation.tool_install_command(pin.requirement, plugins)
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


def _version(version: str) -> Version:
    """`version` parsed; a version that isn't one (a local build) counts as the lowest, so any release is newer."""
    try:
        return Version(version)
    except InvalidVersion:
        return Version("0")
