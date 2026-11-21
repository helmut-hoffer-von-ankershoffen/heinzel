"""The update service's channels: stable, edge and main, and the hint that names the next release."""

import shlex

from packaging.version import Version

from heinzel import HeinzelError, __version__
from heinzel.kernel import (
    CorePin,
)
from heinzel.modules.system._models import UpdateResult
from heinzel.modules.system._service import DEFAULT_REPOSITORY, RELEASE_TIMEOUT
from heinzel.modules.system.updater._update_install import UpdateInstall
from heinzel.modules.system.updater._update_rules import DEFAULT_BRANCH, PLACEHOLDER_TAG, UpdateChannel, parse_version


class UpdateChannels(UpdateInstall):
    """Moves the installation within or between the update channels."""

    def _asked_latest(self, current: str) -> str | None:
        try:
            return self._latest(current)
        # Isolation boundary: whatever the package source raises, the plan says it couldn't be asked.
        except Exception:  # noqa: BLE001
            return None

    def _update(self, source: str | None, current: str, channel: UpdateChannel) -> UpdateResult:
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
            latest = self._asked_latest(current) if pin.git is None else None
            return self._install(
                pin.requirement, pin, plugins, current=current, latest=latest, target=None, generation=generation
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
        if Version(edge.version) <= parse_version(current):
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
        if Version(stable) < parse_version(current):
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
                if Version(newest) <= parse_version(current):
                    return f"hz {current} stays: the newest edge release, {newest}, is not newer."
                return f"That update moves hz {current} to edge {newest}: every green change, sooner and less tested."
            if chosen == "main":
                return f"That update moves hz {current} to the newest commit of main."
            stable = self._releases.highest_version(RELEASE_TIMEOUT, prereleases=False)
        # Isolation boundary: not knowing the versions must not stop the choice; the statement says so.
        except Exception as exc:  # noqa: BLE001
            return f"The newest {chosen} version could not be asked for now ({exc})."
        if Version(stable) < parse_version(current):
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
        if Version(latest) < parse_version(current):
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
        if Version(latest) <= parse_version(current):
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
        if Version(latest) <= parse_version(after):
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
