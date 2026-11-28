"""Guard daemon uninstall and offline package removal using the owned service files."""

import os
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from heinzel import HeinzelError
from heinzel.kernel import Installation, MaintenanceLocks, private_lock
from heinzel.kernel.install._tool_uninstall import OwnedFile, owned_file, remove_owned_file
from heinzel.modules.system.daemon._handover_files import safely_absent as _safely_absent
from heinzel.modules.system.daemon._mutation_scope import DaemonRemoval
from heinzel.modules.system.daemon._ownership import ManagerState, tick_environment
from heinzel.modules.system.daemon._runtime import fsync_dir
from heinzel.modules.system.daemon._uninstall_history import UninstallHistory
from heinzel.modules.system.daemon._units import LABEL, SYSTEMD_NAME

if TYPE_CHECKING:
    import subprocess


class OfflineRemoval:
    """Removal operations for DaemonService; its state lives on the service."""

    if TYPE_CHECKING:
        _platform: str
        _profile: dict[str, str]
        _quiescence_installation: Installation | None
        _uid: Callable[[], int]
        state_dir: Path

        def _capture_install_files(self) -> dict[Path, bytes | None]: ...
        def _handover_marker_valid(self, recorded: dict[str, str | int]) -> bool: ...
        def _handover_pending(self) -> bool: ...
        def _mutation_token_matches(self, token: str, name: str = "mutation-pending.json") -> bool: ...
        def _read_state(self, name: str) -> dict[str, str | int]: ...
        def _quiescent_snapshot(
            self,
            binary: Path,
            token: str | None = None,
            *,
            gate: Path | None = None,
            compatibility_gates: tuple[Path, ...] = (),
        ) -> bool: ...
        def _unit_paths(self) -> list[Path]: ...
        def owned_manager_command(self, command: list[str]) -> subprocess.CompletedProcess[str]: ...
        def owned_manager_state(self, binary: Path) -> ManagerState: ...
        def owned_unit_state(self, binary: Path, *, allow_absent: bool = False) -> bool: ...

    def _capture_uninstall_history(self, before: dict[Path, bytes | None]) -> UninstallHistory | None:
        history = None
        if not _safely_absent(self.state_dir / "handover-request.json"):
            history = UninstallHistory.capture(self.state_dir, before)
        if not self._handover_marker_valid(self._read_state("install.json")):
            msg = "The previous handover cannot be verified; its files were retained before uninstalling."
            raise HeinzelError(msg)
        return history

    def _archive_uninstall_history(self, history: UninstallHistory | None, token: str) -> None:
        if not self._mutation_token_matches(token, "handover-pending.json"):
            msg = "The daemon removal writer changed; its files and pending gate were retained."
            raise HeinzelError(msg)
        if history is not None:
            history.archive(self.state_dir / f"uninstall-{token}")
        elif not _safely_absent(self.state_dir / "handover-request.json"):
            msg = "A handover appeared during uninstall; its files and pending gate were retained."
            raise HeinzelError(msg)

    def _remove_install_files(self, before: dict[Path, bytes | None]) -> list[Path]:
        removed: list[Path] = []
        expected = dict(before)
        for path, payload in before.items():
            if self._capture_install_files() != expected:
                msg = "The owned daemon files changed during removal; explicit recovery is required."
                raise HeinzelError(msg)
            if payload is not None:
                path.unlink()
                fsync_dir(path.parent)
                if path.name != "install.json":
                    removed.append(path)
            expected[path] = None
        return removed

    def _offline_probe(self, command: list[str]) -> str | None:
        result = self.owned_manager_command(command)
        if result.returncode != 0:
            return None
        return result.stdout

    def _offline_files(self, binary: Path) -> list[OwnedFile]:
        if not self.owned_unit_state(binary, allow_absent=True):
            msg = "The daemon's service files are foreign or changed; they were left unchanged."
            raise HeinzelError(msg)
        paths = [*self._unit_paths(), self.state_dir / "install.json"]
        return [owned_file(path) for path in paths if os.path.lexists(path)]

    def _offline_stop(
        self,
        binary: Path,
        files: list[OwnedFile],
        state: ManagerState,
        token: str,
        *,
        fence: tuple[Path | None, tuple[Path, ...]] = (None, ()),
    ) -> None:
        gate, compatibility_gates = fence
        if any(owned_file(file.path) != file for file in files) or self.owned_manager_state(binary) != state:
            msg = "The daemon changed before stopping; it was left unchanged."
            raise HeinzelError(msg)
        if state.registered:
            command = (
                ["/bin/launchctl", "bootout", f"gui/{self._uid()}/{LABEL}"]
                if self._platform == "darwin"
                else [
                    "/usr/bin/systemctl",
                    "--user",
                    "disable",
                    "--now",
                    f"{SYSTEMD_NAME}.timer",
                    f"{SYSTEMD_NAME}.service",
                ]
            )
            if self._offline_probe(command) is None:
                msg = "The owned daemon could not be stopped; its files and installation were retained."
                raise HeinzelError(msg)
        if not self._quiescent_snapshot(binary, token, gate=gate, compatibility_gates=compatibility_gates):
            msg = (
                "The daemon is not proven quiescent; the installation was retained. Inspect its local service manager."
            )
            raise HeinzelError(msg)

    def _offline_manager_units(self, binary: Path, files: list[OwnedFile]) -> dict[str, dict[str, object]]:
        from heinzel.modules.system.daemon._units import TICK  # noqa: PLC0415 - shared fixed argv

        if self._platform != "linux" or not files:
            return {}
        service, timer = self._unit_paths()
        executable = binary / "hz"
        environment = tick_environment(executable, self._profile)
        return {
            service.name: {
                "fragment": str(service),
                "program": str(executable),
                "arguments": f"{executable} {' '.join(TICK)}",
                "environment": environment,
                "directory": environment["HOME"],
            },
            timer.name: {"fragment": str(timer), "triggers": service.name, "disabled": True},
        }

    @contextmanager
    def offline_removal_guard(
        self,
        binary: Path,
        *,
        locks: MaintenanceLocks | None = None,
        gate: Path | None = None,
        compatibility_gates: tuple[Path, ...] = (),
    ) -> Generator[DaemonRemoval]:
        """Hold daemon lifecycle/tick authority while a durable coordinator stops and removes one tool.

        Yields:
            Captured owned definitions and separate stop/final-cleanup actions.

        Raises:
            HeinzelError: If ownership, quiescence or final cleanup cannot be established.
            OSError: If physical local authority cannot be inspected.
        """
        lifecycle, tick = self.state_dir / "lifecycle.lock", self.state_dir / "tick.lock"
        with (
            locks.guard(lifecycle) if locks is not None else private_lock(lifecycle),
            locks.guard(tick) if locks is not None else private_lock(tick),
        ):
            if self._handover_pending():
                msg = "A Heinzel lifecycle change requires recovery; nothing was uninstalled."
                raise HeinzelError(msg)
            files = self._offline_files(binary)
            state = self.owned_manager_state(binary)

            def stop(token: str) -> None:
                self._offline_stop(binary, files, state, token, fence=(gate, compatibility_gates))

            def finish() -> None:
                if self._quiescence_installation is None or not _safely_absent(self._quiescence_installation.prefix):
                    msg = "The Heinzel tool removal is incomplete; its stopped service files were retained."
                    raise HeinzelError(msg)
                for captured in files:
                    remove_owned_file(captured)
                if (
                    self._platform == "linux"
                    and self._offline_probe(["/usr/bin/systemctl", "--user", "daemon-reload"]) is None
                ):
                    msg = "Heinzel was removed, but the user manager needs a local reload; user data was kept."
                    raise HeinzelError(msg)

            yield DaemonRemoval(tuple(files), stop, finish, self._offline_manager_units(binary, files))
