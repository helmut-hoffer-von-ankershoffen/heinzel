"""The daemon's quiescence: proof that no unit runs or is queued before an install or removal touches it."""

import json
import os
import re
import secrets
import stat
import subprocess
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from heinzel import HeinzelError
from heinzel.kernel import Installation, ProcessOutcome, host_maintenance_gate, private_read
from heinzel.modules.system.daemon._abandoned_mutation import marker_token_matches
from heinzel.modules.system.daemon._handover_files import _open_verified_handover_file, _safely_absent
from heinzel.modules.system.daemon._manager_commands import (
    _launchctl_quiescent_output,
    _launchctl_records,
    _systemd_quiescent_unit,
)
from heinzel.modules.system.daemon._ownership import (
    ManagerState,
    launchd_owned,
    profile_digest,
    systemd_owned,
    tick_environment,
)
from heinzel.modules.system.daemon._runtime import lock
from heinzel.modules.system.daemon._units import LABEL, PLATFORMS, SYSTEMD_NAME, plist_path, systemd_paths


class _Quiescence:
    """Quiescence and recovery checks of DaemonService; their state lives on the service."""

    if TYPE_CHECKING:
        _clock: Callable[[], datetime]
        _linux_manager_identity: Callable[[], tuple[int, ...] | None]
        _platform: str
        _profile: dict[str, str]
        _quiescence_advice: str | None
        _quiescence_installation: Installation | None
        _quiescence_probe: Callable[[list[str]], ProcessOutcome]
        _quiescence_problem: str
        _uid: Callable[[], int]
        state_dir: Path
        unit_dir: Path

        def _handover_pending(self) -> bool: ...
        def _handover_signature(self, marker_override: bytes | None = None) -> str: ...
        @contextmanager
        def _lifecycle_writer(self) -> Generator[None]: ...
        def _read_state(self, name: str) -> dict[str, str | int]: ...
        def _unit_files(self, executable: Path, minutes: int) -> dict[Path, str]: ...
        def _unit_paths(self) -> list[Path]: ...
        def _write_state(self, name: str, data: dict[str, str | int]) -> None: ...

    def _mutation_token_matches(self, token: str, name: str = "mutation-pending.json") -> bool:
        try:
            with _open_verified_handover_file(self.state_dir / name) as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
                    return False
                raw = handle.read(256)
                if name == "mutation-pending.json":
                    return marker_token_matches(raw, token)
                return raw == (json.dumps({"token": token}, indent=2) + "\n").encode()
        except (OSError, HeinzelError):
            return False

    def _quiescence_target(self, binary: Path) -> bool:
        installation = self._quiescence_installation
        if installation is None or installation.prefix.name != "heinzel" or binary.is_relative_to(installation.prefix):
            return False
        prefix = installation.prefix
        store = prefix.parent
        try:
            for path in (store, binary):
                info = path.lstat()
                if (
                    not path.is_absolute()
                    or path.resolve(strict=True) != path
                    or not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o022
                ):
                    return False
            if _safely_absent(prefix):
                return all(_safely_absent(binary / name) for name in ("hz", "heinzel", "hz-install"))
            info = prefix.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o022
                or prefix.resolve(strict=True) != prefix
            ):
                return False
            installation.verify_tool_destination(store, binary)
        except (OSError, HeinzelError):
            return False
        else:
            return True

    @property
    def recovery_platform(self) -> str:
        """The platform selected by this daemon authority."""
        return self._platform

    @property
    def recovery_profile(self) -> dict[str, str]:
        """A copy of the selected profile routing for explicit recovery inspection."""
        return dict(self._profile)

    @property
    def recovery_installation(self) -> Installation | None:
        """The existing installation bound to this daemon's quiescence checks."""
        return self._quiescence_installation

    def recovery_uid(self) -> int:
        """Observe the same user identity used by normal daemon authority checks."""
        return self._uid()

    def recovery_now(self) -> datetime:
        """Observe this service's clock for completed-history validation."""
        return self._clock()

    def recovery_token_matches(self, token: str, name: str) -> bool:
        """Check the exact existing mutation token without modifying its gate."""
        return self._mutation_token_matches(token, name)

    @contextmanager
    def recovery_writer(self) -> Generator[None]:
        """Hold the same lifecycle writer used by ordinary daemon mutations."""
        with self._lifecycle_writer():
            yield

    def owned_manager_command(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        """Use only the fixed, bounded manager in this verified user session, before and after the request.

        Raises:
            HeinzelError: If manager authority changes or the request cannot be observed completely.
        """
        expected = "/bin/launchctl" if self._platform == "darwin" else "/usr/bin/systemctl"
        if self._platform not in PLATFORMS or not command or command[0] != expected:
            msg = "The daemon request is not addressed to the fixed local user manager."
            raise HeinzelError(msg)
        identity = self._linux_manager_identity() if self._platform == "linux" else None
        if (self._platform == "linux" and identity is None) or (
            self._platform == "darwin" and not self._mac_session_verified()
        ):
            msg = "The daemon's user service manager cannot be verified; nothing was changed."
            raise HeinzelError(msg)
        result = self._quiescence_probe(command)
        stable = (
            self._linux_manager_identity() == identity if self._platform == "linux" else self._mac_session_verified()
        )
        if not stable or result.truncated or result.returncode is None:
            msg = "The daemon manager request is uncertain; inspect its actual state before retrying."
            raise HeinzelError(msg)
        return subprocess.CompletedProcess(command, result.returncode, result.stdout, result.stderr)

    def owned_unit_state(self, binary: Path, *, allow_absent: bool = False) -> bool:
        """Whether exact safe files, receipt, profile and tool authority agree; absence is explicit."""
        present = not all(_safely_absent(path) for path in (*self._unit_paths(), self.state_dir / "install.json"))
        return (present or allow_absent) and self._quiescence_unit_state(binary)

    def owned_manager_state(self, binary: Path) -> ManagerState:
        """Observe an owned registration and activation, or verified absence, with its current tick PID.

        Raises:
            HeinzelError: If files, manager argv/profile or user-session authority cannot be verified.
        """
        if not self.owned_unit_state(binary, allow_absent=True):
            msg = "The daemon files belong to another profile or were changed; nothing was changed."
            raise HeinzelError(msg)
        executable = binary / "hz"
        environment = tick_environment(executable, self._profile)
        if self._platform == "darwin":
            result = self.owned_manager_command(["/bin/launchctl", "print", f"gui/{self._uid()}/{LABEL}"])
            if result.returncode != 0:
                records = self.owned_manager_command(["/bin/launchctl", "list"])
                listing = _launchctl_records(records.stdout) if records.returncode == 0 else None
                if listing is None or LABEL in listing:
                    msg = "The daemon's manager registration is unknown; nothing was changed."
                    raise HeinzelError(msg)
                return ManagerState(registered=False, active=False, enabled=False)
            state = launchd_owned(result.stdout, plist_path(self.unit_dir), executable, environment)
        else:
            outputs: list[str] = []
            for unit in (f"{SYSTEMD_NAME}.service", f"{SYSTEMD_NAME}.timer"):
                result = self.owned_manager_command(
                    [
                        "/usr/bin/systemctl",
                        "--user",
                        "show",
                        unit,
                        "--property=Id,LoadState,FragmentPath,DropInPaths,ExecStart,Triggers,Environment,WorkingDirectory,ActiveState,UnitFileState,MainPID,ControlPID,Job",
                    ]
                )
                if result.returncode != 0:
                    msg = "The daemon's user manager unit cannot be inspected; nothing was changed."
                    raise HeinzelError(msg)
                outputs.append(result.stdout)
            state = systemd_owned(outputs[0], outputs[1], systemd_paths(self.unit_dir), executable, environment)
        if state.registered and not self.owned_unit_state(binary):
            msg = "A daemon label is loaded without this profile's exact owned files; nothing was changed."
            raise HeinzelError(msg)
        return state

    def _quiescence_unit_state(self, binary: Path) -> bool:
        paths = self._unit_paths()
        marker = self.state_dir / "install.json"
        absent = [_safely_absent(path) for path in paths]
        marker_absent = _safely_absent(marker)
        if all(absent) and marker_absent:
            return True
        if any(absent) or marker_absent or self._quiescence_installation is None:
            return False
        try:
            self._handover_signature()
            recorded = self._read_state("install.json")
            executable = binary / "hz"
            minutes = recorded.get("interval_minutes")
            if (
                recorded.get("profile") != profile_digest(self._profile)
                or recorded.get("executable") != str(executable)
                or recorded.get("platform") != self._platform
                or type(minutes) is not int
                or minutes <= 0
            ):
                return False
            # On macOS uv uninstall removes the tool environment and shims, retaining an owned stopped unit.
            # The enclosing snapshot still requires a trusted empty destination and actual manager absence.
            if self._platform != "darwin" or not _safely_absent(self._quiescence_installation.prefix):
                self._quiescence_installation.verify_tool_destination(
                    self._quiescence_installation.prefix.parent, binary
                )
            return all(path.read_text() == content for path, content in self._unit_files(executable, minutes).items())
        except (OSError, ValueError, HeinzelError):  # UnicodeError and a marker's impossible interval are ValueErrors
            return False

    def _recover_retained_unit(self, binary: Path) -> None:
        """Retain only a profile-bound owned stopped unit; unknown legacy definitions are never retired.

        A released byte format and a dead PID do not establish which profile or loaded job owns a fixed label.
        Recovery never bootouts, moves or deletes those definitions on the basis of that weaker evidence.
        """
        self._quiescence_advice = None
        if all(_safely_absent(path) for path in (*self._unit_paths(), self.state_dir / "install.json")):
            return
        self._quiescence_advice = (
            "The retained daemon definition or its profile cannot be verified. Inspect the user service manager, "
            "the selected configuration and existing unit before changing them; "
            "no definition was moved or unloaded. "
            "For an older daemon, follow the explicit recovery guide: https://heinzel.team/DAEMON_RECOVERY/."
        )
        if self.owned_unit_state(binary):
            self._quiescence_advice = None

    def _quiescent_snapshot(
        self,
        binary: Path,
        token: str | None = None,
        *,
        gate: Path | None = None,
        compatibility_gates: tuple[Path, ...] = (),
    ) -> bool:
        self._quiescence_problem = "The selected installation destination could not be verified."
        if self._platform not in {"darwin", "linux"} or not self._quiescence_target(binary):
            return False
        paths = [
            self.state_dir / name
            for name in ("handover-pending.json", "handover-request.json", "mutation-pending.json")
        ]

        def pending_valid() -> bool:
            if gate is not None:
                installation = self._quiescence_installation
                return (
                    installation is not None
                    and gate == host_maintenance_gate(installation.prefix)
                    and (
                        private_read(self.state_dir / "standard-install-pending.json")
                        == json.dumps({"token": token}, indent=2) + "\n"
                        if compatibility_gates == (self.state_dir / "standard-install-pending.json",)
                        else not compatibility_gates
                        and _safely_absent(self.state_dir / "standard-install-pending.json")
                    )
                    and private_read(gate) == json.dumps({"token": token}, indent=2) + "\n"
                )
            if self._quiescence_installation is not None and not _safely_absent(
                host_maintenance_gate(self._quiescence_installation.prefix)
            ):
                return False
            if token is None:
                return _safely_absent(self.state_dir / "standard-install-pending.json")
            return self._mutation_token_matches(token, "standard-install-pending.json")

        self._quiescence_problem = "A previous daemon or installation transaction requires recovery."
        if not (pending_valid() and all(_safely_absent(path) for path in paths)):
            return False
        self._quiescence_problem = "The retained daemon unit or install receipt could not be verified."
        if not self._quiescence_unit_state(binary):
            return False
        if self._platform == "linux":
            return self._linux_quiescent_snapshot(binary, pending_valid, paths)
        if not self._mac_manager_absent():
            return False
        self._quiescence_problem = "The installation or daemon evidence changed during verification."
        return (
            pending_valid()
            and all(_safely_absent(path) for path in paths)
            and self._quiescence_unit_state(binary)
            and self._quiescence_target(binary)
        )

    def _mac_session_verified(self) -> bool:
        try:
            results = [
                self._quiescence_probe(command)
                for command in (["/bin/launchctl", "managername"], ["/bin/launchctl", "manageruid"])
            ]
        except (OSError, ValueError, HeinzelError):
            return False
        name, uid = results
        return (
            name.returncode == uid.returncode == 0
            and name.stdout.strip() == "Aqua"
            and uid.stdout.strip() == str(self._uid())
        )

    def _mac_manager_absent(self) -> bool:
        commands = [
            ["/bin/launchctl", "managername"],
            ["/bin/launchctl", "manageruid"],
            ["/bin/launchctl", "list"],
            ["/bin/launchctl", "managername"],
            ["/bin/launchctl", "manageruid"],
        ]
        self._quiescence_problem = "macOS service-manager inspection failed or returned incomplete evidence."
        try:
            results = [self._quiescence_probe(command) for command in commands]
            if any(result.returncode != 0 or result.truncated for result in results):
                return False
            name1, uid1, listing, name2, uid2 = [result.stdout for result in results]
            if not (name1.strip() == name2.strip() == "Aqua" and uid1.strip() == uid2.strip() == str(self._uid())):
                self._quiescence_problem = (
                    "The current process is not in a verified macOS desktop session for this user."
                )
                return False
            self._quiescence_problem = (
                "macOS did not confirm daemon absence; the service is loaded or its list is invalid."
            )
            return _launchctl_quiescent_output(listing)
        except (OSError, ValueError, HeinzelError):
            return False

    def _linux_quiescent_snapshot(self, binary: Path, pending_valid: Callable[[], bool], paths: list[Path]) -> bool:
        identity = self._linux_manager_identity()
        if identity is None:
            return False
        manager = ["/usr/bin/systemctl", "--user", "--no-pager", "show", "--all", "--property=Version"]
        units = [f"{SYSTEMD_NAME}.service", f"{SYSTEMD_NAME}.timer"]
        commands = [manager]
        commands.extend(
            [
                "/usr/bin/systemctl",
                "--user",
                "--no-pager",
                "show",
                "--all",
                "--property=Id,LoadState,ActiveState,SubState,Job,MainPID,ControlPID",
                unit,
            ]
            for unit in (*units, *units)
        )
        commands.append(manager)
        try:
            results = [self._quiescence_probe(command) for command in commands]
            if any(result.returncode != 0 or result.truncated for result in results):
                return False
            first, *observations, last = [result.stdout for result in results]
            return (
                bool(re.fullmatch(r"Version=[^\r\n=]{1,128}\n?", first))
                and first == last
                and all(
                    _systemd_quiescent_unit(output, unit)
                    for output, unit in zip(observations, (*units, *units), strict=True)
                )
                and self._linux_manager_identity() == identity
                and pending_valid()
                and all(_safely_absent(path) for path in paths)
                and self._quiescence_unit_state(binary)
                and self._quiescence_target(binary)
            )
        except (OSError, ValueError, HeinzelError):
            return False

    @contextmanager
    def quiescence_guard(self, binary: Path) -> Generator[None]:
        """Hold lifecycle and tick locks through a trusted, quiescent standard installation.

        Non-GUI, unsupported or malformed manager evidence denies. This does not fence noncooperating OS actors or
        interpreter startup. The selected Installation must be bound before resolving this service.

        Yields:
            Control while cooperating daemon dispatch and lifecycle changes are fenced.

        Raises:
            HeinzelError: If a lock is busy or daemon quiescence cannot be established.
        """
        with lock(self.state_dir / "lifecycle.lock") as lifecycle:
            if not lifecycle or self._handover_pending():
                msg = "The daemon lifecycle is changing or requires recovery."
                raise HeinzelError(msg)
            with lock(self.state_dir / "tick.lock") as tick:
                if tick and self._platform == "linux" and self._linux_manager_identity() is None:
                    msg = (
                        "The daemon is not proven quiescent because your Linux user manager is unavailable or "
                        "cannot be verified. Open a user session with its service manager and retry; "
                        "inspect `hz system daemon status` if needed."
                    )
                    raise HeinzelError(msg)
                if tick and self._platform == "darwin" and self._quiescence_installation is not None:
                    with suppress(OSError, ValueError, HeinzelError):  # the snapshot below still decides
                        self._recover_retained_unit(binary)
                if not tick or not self._quiescent_snapshot(binary):
                    if self._platform == "linux":
                        msg = (
                            "The daemon is not proven quiescent. In your Linux user session, check that the user "
                            "service manager is available and inspect `hz system daemon status`; ensure both the "
                            "service and timer are stopped before retrying."
                        )
                    elif self._platform == "darwin":
                        problem = self._quiescence_problem if tick else "The daemon tick lock is busy."
                        msg = (
                            f"The daemon is not proven quiescent. {problem} "
                            f"{self._quiescence_advice + ' ' if self._quiescence_advice else ''}"
                            "Inspect `uvx --from heinzel hz system daemon status` before retrying. "
                            "This inspection command also works when hz is not installed."
                        )
                    else:
                        msg = "The daemon is not proven quiescent on this platform; inspect it before installing."
                    raise HeinzelError(msg)
                token = secrets.token_hex(32)
                self._write_state("standard-install-pending.json", {"token": token})
                yield
                self._finish_quiescent_install(binary, token)

    def _finish_quiescent_install(self, binary: Path, token: str) -> None:
        installation = self._quiescence_installation
        name = "standard-install-pending.json"
        if installation is None or not self._mutation_token_matches(token, name):
            msg = "The installer transaction changed; explicit recovery is required."
            raise HeinzelError(msg)
        fingerprint = installation.startup_fingerprint()
        if (
            not re.fullmatch(r"[a-f0-9]{64}", fingerprint)
            or not installation.startup_state_matches()
            or installation.startup_fingerprint() != fingerprint
            or not self._quiescent_snapshot(binary, token)
            or installation.startup_fingerprint() != fingerprint
            or not installation.startup_state_matches()
            or not self._mutation_token_matches(token, name)
        ):
            msg = "The installation or daemon could not be verified; explicit recovery is required."
            raise HeinzelError(msg)
        (self.state_dir / name).unlink()

    def recover_quiescent_install(self, binary: Path) -> None:
        """Explicitly clear a standard-install interruption only after independently restored evidence matches.

        Raises:
            HeinzelError: If the record, installation or manager state cannot be verified.
        """
        with lock(self.state_dir / "lifecycle.lock") as lifecycle, lock(self.state_dir / "tick.lock") as tick:
            if not lifecycle or not tick:
                msg = "The daemon lifecycle is changing; retry recovery."
                raise HeinzelError(msg)
            try:
                with _open_verified_handover_file(self.state_dir / "standard-install-pending.json") as handle:
                    data = json.loads(handle.read(256))
                token = data["token"]
            except (OSError, ValueError, KeyError, TypeError, HeinzelError) as exc:
                msg = "The installer recovery record cannot be verified."
                raise HeinzelError(msg) from exc
            if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{64}", token):
                msg = "The installer recovery record cannot be verified."
                raise HeinzelError(msg)
            self._finish_quiescent_install(binary, token)
