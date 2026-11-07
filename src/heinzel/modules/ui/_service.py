"""Opt-in OS ownership of the foreground web server, independent of scheduled daemon ticks."""

import json
import os
import plistlib
import re
import secrets
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field, JsonValue, TypeAdapter

from heinzel import Config, HeinzelError, Kernel, Settings
from heinzel.kernel import (
    HostServiceManager,
    Installation,
    MaintenanceLocks,
    PrivateControl,
    boot,
    host_maintenance_gate,
    host_maintenance_lock,
    installation_mutation_paths,
    installation_mutation_pending,
    private_directory,
    private_lock,
    private_read,
    private_socket_directory,
    private_write,
)
from heinzel.kernel._offline_uv import verified_installed_path
from heinzel.kernel._tool_uninstall import OwnedFile, owned_file, remove_owned_file
from heinzel.modules.ui._historical_launcher import HistoricalLauncher
from heinzel.modules.ui._launcher_transition import Action, LauncherTransition
from heinzel.modules.ui._profile import (
    default_profile,
    environment_command,
    installation_overrides,
    require_installation_profile,
    user_profile,
    validated_profile,
)

LABEL = "io.github.helmut-hoffer-von-ankershoffen.heinzel.web"
UNIT = "hz-web.service"
MAX_PORT = 65535
STOP_SECONDS = 5.0
STOP_POLL_SECONDS = 0.05
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    # The manager executable is resolved and its argument vector is fixed by the operation.
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=10)  # noqa: S603


class WebStatus(BaseModel):
    """Observed OS ownership and live authenticated server identity; never contains a bearer credential."""

    installed: bool = Field(description="Exact owned integration files and private installation record exist.")
    loaded: bool = Field(description="The service manager identifies the exact installed service.")
    ready: bool = Field(description="Its live private endpoint confirms PID, generation, port and web instance.")
    port: int | None = Field(default=None, description="Fixed loopback port installed for this service.")
    pid: int | None = Field(default=None, description="Current PID reported by the exact owned service manager.")
    instance: str | None = Field(default=None, description="The live web lifecycle instance, when ready.")
    version: str | None = Field(default=None, description="Code loaded by that live web server, when ready.")
    problem: str | None = Field(default=None, description="A safe ownership or readiness problem.")


class WebRestart(BaseModel):
    """A restart request is distinct from observing its replacement."""

    requested: bool = Field(description="The owned server accepted the restart request.")
    retiring_instance: str = Field(description="The instance accepting the request; a replacement must differ.")
    message: str = Field(description="How to independently verify the replacement.")


class WebChange(BaseModel):
    """An explicit OS integration change; loaded and ready are distinct observations."""

    changed: bool = Field(description="Whether integration changed.")
    dry_run: bool = Field(default=False, description="Whether no changes were made.")
    files: dict[str, str] = Field(
        default_factory=dict, description="Exact integration files, containing no credentials."
    )
    status: WebStatus = Field(description="The independently observed service state.")


class WebStop(Protocol):
    """Stopping a historical launcher additionally requires the coordinator's exact compatibility token."""

    def __call__(self, token: str | None = None) -> None:
        """Stop only after every required fence is owned by this operation."""
        ...


@dataclass(frozen=True)
class WebRemoval:
    """One held, preflighted integration: stopping and final file cleanup are separate operations."""

    files: tuple[OwnedFile, ...]
    stop: WebStop
    finish: Callable[[], WebChange]
    manager_units: dict[str, dict[str, object]]
    compatibility_gates: tuple[Path, ...] = ()


class WebService:
    """Install and mutate only exact recorded OS integration for one user and stable uv shim."""

    def __init__(  # noqa: PLR0913 - isolated OS and installation seams
        self,
        state: Path,
        home: Path,
        executable: Path,
        *,
        platform: str = sys.platform,
        runner: Runner = _run,
        which: Callable[[str], str | None] = shutil.which,
        uid: int | None = None,
        verify: Callable[[], bool] = lambda: False,
        prefix: Path | None = None,
        uv: Path | None = None,
        profile: dict[str, str] | None = None,
        unsupported: tuple[str, ...] = (),
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Keep the stable shim path unresolved, because uv replaces its destination during updates."""
        self.directory = state / "ui"
        self.home = home
        self.executable = executable
        self.prefix = prefix or home / "tool"
        self.uv = uv or Path(shutil.which("uv") or "/usr/bin/uv")
        self.profile = profile if profile is not None else default_profile(home, state, self.prefix, executable)
        self.unsupported = unsupported
        self._clock, self._sleep = clock, sleep
        self.platform = platform
        self.runner = runner
        self.which = which
        self.uid = os.getuid() if uid is None else uid
        self.verify = verify
        self.launcher = self.directory / "launch.sh"
        self.marker = self.directory / "service.json"
        self.runtime = self.directory / "runtime.json"
        self.unit = (
            home / "Library/LaunchAgents" / f"{LABEL}.plist"
            if platform == "darwin"
            else home / ".config/systemd/user" / UNIT
        )

    @classmethod
    def for_user(cls, kernel: Kernel) -> "WebService":
        """Use user scope and the current installation's verified stable uv bin entry point.

        Raises:
            HeinzelError: If the selected standard tool or fixed user-manager authority cannot be verified.
        """
        config = Config.load(Path.home())
        settings = config.settings(Settings, "hz")
        if not settings.state_dir.is_absolute():
            msg = "Persistent web availability requires an absolute hz.state_dir in your user configuration."
            raise HeinzelError(msg)
        installation = kernel.resolve(Installation)
        _store, binary = installation.tool_destination()
        uv = installation.prefix / "bin/uv"
        manager = boot(builtin_only=True, cwd=Path.home(), maintenance=True).resolve_builtin_service(
            HostServiceManager, "system.daemon"
        )
        return cls(
            settings.state_dir,
            Path.home(),
            binary / "hz",
            prefix=installation.prefix.resolve(strict=True),
            uv=uv,
            runner=lambda command: manager.command(list(command)),
            which=lambda name: "/bin/launchctl" if name == "launchctl" else "/usr/bin/systemctl",
            profile=user_profile(
                config, settings, Path.home(), installation.prefix.resolve(strict=True), binary / "hz"
            ),
            unsupported=installation_overrides(config),
            verify=lambda: (
                installation.is_tool
                and installation.startup_state_matches()
                and (binary / "hz").samefile(installation.prefix / "bin/hz")
                and verified_installed_path(installation.prefix) == uv
            ),
        )

    @staticmethod
    def removal_uv_hint(state: Path) -> Path:
        """Read only the historical launcher's uv spelling; removal never executes or resolves this path.

        Raises:
            HeinzelError: If an existing integration has no bounded absolute historical spelling.
        """
        try:
            record = TypeAdapter(dict[str, JsonValue]).validate_json(private_read(state / "ui/service.json"))
        except FileNotFoundError:
            return Path("/usr/bin/uv")  # No record: only absent-integration inspection uses this inert value.
        value = record.get("uv")
        if not isinstance(value, str) or not Path(value).is_absolute() or len(value) > 4 * 1024 or "\x00" in value:
            msg = "The recorded web installation identity changed; it was left untouched."
            raise HeinzelError(msg)
        return Path(value)

    def _command(self, *arguments: str) -> list[str]:
        manager = "launchctl" if self.platform == "darwin" else "systemctl"
        executable = self.which(manager)
        if self.platform not in {"darwin", "linux"} or executable is None:
            msg = "Persistent web access requires macOS launchd or Linux systemd user services."
            raise HeinzelError(msg)
        return [executable, *arguments] if self.platform == "darwin" else [executable, "--user", *arguments]

    def _launch_content(
        self, port: int, generation: str, profile: dict[str, str] | None = None, *, legacy: bool = False
    ) -> str:
        guards = "".join(
            f"if [ -e {shlex.quote(str(path))} ] || [ -L {shlex.quote(str(path))} ]; then exit 75; fi\n"
            for path in installation_mutation_paths(self.prefix, self.directory.parent)
        )
        directories = dict.fromkeys(
            [
                str(self.uv.parent),
                str(self.executable.parent),
                "/opt/homebrew/bin",
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
            ]
        )
        path = shlex.quote(":".join(directories))
        arguments = shlex.join(
            [
                str(self.executable),
                "ui",
                *([] if legacy else ["serve"]),
                "--no-browser",
                "--port",
                str(port),
                "--supervised",
                generation,
            ]
        )
        return (
            "#!/bin/sh\nset -eu\n"
            "# Recovery requires the existing update authority before any mutable tool code imports.\n"
            + guards
            + environment_command(self.profile if profile is None else profile)
            + f'exec "$@" PATH={path} {arguments}\n'
        )

    def _content(self) -> str:
        arguments = ["/bin/sh", str(self.launcher)]
        if self.platform == "darwin":
            return plistlib.dumps(
                {
                    "Label": LABEL,
                    "ProgramArguments": arguments,
                    "WorkingDirectory": str(self.home),
                    "RunAtLoad": True,
                    "KeepAlive": True,
                    "ThrottleInterval": 10,
                    "ProcessType": "Background",
                    # No session URL or arbitrary startup/plugin error reaches an unrotated OS log.
                    "StandardOutPath": "/dev/null",
                    "StandardErrorPath": "/dev/null",
                    "EnvironmentVariables": {
                        "PATH": f"{self.executable.parent}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
                    },
                },
                sort_keys=True,
            ).decode()
        command = shlex.join(arguments).replace("%", "%%").replace("$", "$$")
        directory = shlex.quote(str(self.home)).replace("%", "%%")
        return (
            "[Unit]\nDescription=Heinzelmännchen web workspace\nStartLimitIntervalSec=0\n\n"
            "[Service]\nType=simple\n"
            f"ExecStart={command}\nWorkingDirectory={directory}\n"
            "Restart=always\nRestartSec=10\nStandardOutput=null\nStandardError=null\n\n"
            "[Install]\nWantedBy=default.target\n"
        )

    def historical_record(self, record: dict[str, JsonValue] | None) -> bool:
        """Recognized historical authority remains distinct from a current operational launcher."""
        if record is None:
            return False
        profile = validated_profile(
            record.get("profile"), self.home, self.directory.parent, self.prefix, self.executable
        )
        return profile is not None and (
            record["uv"] != str(self.uv)
            or record["launcher"] != self._launch_content(int(str(record["port"])), str(record["generation"]), profile)
        )

    def compatibility_gate(self) -> Path:
        """A frozen historical shell consumes this profile fence before importing mutable SDK code."""
        return self.directory.parent / "daemon/standard-install-pending.json"

    def valid_record_value(self, value: dict[str, JsonValue]) -> bool:
        """Validate only exact current or historical tuples; this does not authorize files, a manager or effects."""
        if set(value) != {
            "port",
            "generation",
            "executable",
            "unit",
            "content",
            "launcher",
            "prefix",
            "uv",
            "profile",
        }:
            return False
        profile = validated_profile(
            value.get("profile"), self.home, self.directory.parent, self.prefix, self.executable
        )
        port, generation, uv = value.get("port"), value.get("generation"), value.get("uv")
        if (
            profile is None
            or type(port) is not int
            or not 1 <= port <= MAX_PORT
            or not isinstance(generation, str)
            or re.fullmatch(r"[0-9a-f]{32}", generation) is None
            or value["executable"] != str(self.executable)
            or value["prefix"] != str(self.prefix)
            or value["unit"] != str(self.unit)
            or value["content"] != self._content()
            or not isinstance(uv, str)
            or not Path(uv).is_absolute()
            or len(uv) > 4 * 1024
            or "\x00" in uv
        ):
            return False
        historical = HistoricalLauncher(self.prefix, self.directory, self.executable, Path(uv), profile)
        return (
            value["uv"] == str(self.uv) and value["launcher"] == self._launch_content(port, generation, profile)
        ) or value["launcher"] in {
            historical.render(port, generation),
            historical.render(port, generation, legacy=True),
        }

    def _record(self) -> dict[str, JsonValue] | None:
        try:
            value = TypeAdapter(dict[str, JsonValue]).validate_json(private_read(self.marker))
            if not self.valid_record_value(value) or private_read(self.launcher) != value["launcher"]:
                return None
            info = self.unit.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != self.uid or info.st_mode & 0o022:
                return None
            if self.unit.read_text() != value["content"]:
                return None
        except (OSError, ValueError, HeinzelError):
            return None
        return value

    def _manager(self, record: dict[str, JsonValue] | None) -> tuple[bool, int | None]:
        if self.platform == "darwin":
            return self._manager_darwin(record)
        return self._manager_linux(record)

    def _manager_darwin(self, record: dict[str, JsonValue] | None) -> tuple[bool, int | None]:
        result = self.runner(self._command("print", f"gui/{self.uid}/{LABEL}"))
        if result.returncode != 0:
            # A failed label-specific request is not evidence of absence: require the complete listing.
            listing = self.runner(self._command("list"))
            lines = listing.stdout.splitlines()
            if listing.returncode or not lines or lines[0].split() != ["PID", "Status", "Label"]:
                msg = "The web service manager could not be inspected."
                raise HeinzelError(msg)
            if any(line.split()[-1:] == [LABEL] for line in lines[1:]):
                msg = "The web service label exists but its loaded identity is unknown."
                raise HeinzelError(msg)
            return False, None
        if record is None:
            msg = "An unowned web service is loaded; it was left unchanged."
            raise HeinzelError(msg)
        paths = re.findall(r"(?m)^\s*path = (.+)$", result.stdout)
        expected = plistlib.loads(str(record["content"]).encode())["ProgramArguments"]
        programs = re.findall(r"(?m)^\s*program = (.+)$", result.stdout)
        match = re.search(r"(?ms)^\s*arguments = \{\n(.*?)^\s*\}", result.stdout)
        arguments = [line.strip() for line in match[1].splitlines()] if match else []
        if paths != [str(self.unit)] or programs != ["/bin/sh"] or arguments != expected:
            msg = "The loaded web job differs from its owned integration; it was left unchanged."
            raise HeinzelError(msg)
        pids = re.findall(r"(?m)^\s*pid = ([1-9]\d*)$", result.stdout, flags=re.ASCII)
        return True, int(pids[0]) if len(pids) == 1 else None

    def _manager_linux(self, record: dict[str, JsonValue] | None) -> tuple[bool, int | None]:
        result = self.runner(
            self._command("show", UNIT, "--property=LoadState,FragmentPath,DropInPaths,ExecStart,MainPID")
        )
        if result.returncode != 0:
            msg = "The web service manager could not be inspected."
            raise HeinzelError(msg)
        values = {
            key: value for line in result.stdout.splitlines() if "=" in line for key, value in [line.split("=", 1)]
        }
        if values.get("LoadState") == "not-found":
            return False, None
        if (
            record is None
            or values.get("LoadState") != "loaded"
            or values.get("FragmentPath") != str(self.unit)
            or values.get("DropInPaths") != ""
        ):
            msg = "The loaded web service is unowned or has overrides; it was left unchanged."
            raise HeinzelError(msg)
        # ExecStart's machine property includes argv[]; compare its complete fixed vector, not an executable substring.
        expected = f"/bin/sh {self.launcher}"
        starts = values.get("ExecStart", "")
        prefix = f"{{ path=/bin/sh ; argv[]={expected} ; ignore_errors=no ;"
        if not starts.startswith(prefix) or starts.count("argv[]=") != 1:
            msg = "The loaded web command differs from its owned integration; it was left unchanged."
            raise HeinzelError(msg)
        pid = values.get("MainPID", "")
        return True, int(pid) if pid.isdigit() and int(pid) > 0 else None

    def _control(self, record: dict[str, JsonValue], pid: int, command: str) -> dict[str, JsonValue]:
        content = private_read(self.runtime)
        value = TypeAdapter(dict[str, JsonValue]).validate_json(content)
        if set(value) != {"socket", "pid", "generation", "port", "instance"}:
            msg = "No matching web runtime has reported readiness."
            raise HeinzelError(msg)
        name = value["socket"]
        if (
            not isinstance(name, str)
            or re.fullmatch(r"ctl-[0-9a-f]{12}\.sock", name) is None
            or type(value["pid"]) is not int
            or value["pid"] != pid
            or not isinstance(value["instance"], str)
            or re.fullmatch(r"[0-9a-f]{32}", value["instance"]) is None
            or value["generation"] != record["generation"]
            or value["port"] != record["port"]
        ):
            msg = "The web runtime does not match the loaded service."
            raise HeinzelError(msg)
        response = PrivateControl.request(private_socket_directory(self.directory) / name, command)
        if any(response.get(key) != value[key] for key in ("pid", "generation", "port", "instance")):
            msg = "The live web runtime identity does not match the loaded service."
            raise HeinzelError(msg)
        if response.get("error") == "mutation-pending":
            msg = "An installation change requires completion or recovery before restarting the web service."
            raise HeinzelError(msg)
        return response

    def status(self) -> WebStatus:
        """Inspect exact unit ownership and the running server through its private control channel."""
        record = self._record()
        try:
            loaded, pid = self._manager(record)
        except (OSError, HeinzelError, subprocess.SubprocessError):
            return WebStatus(
                installed=record is not None,
                loaded=False,
                ready=False,
                problem="Service ownership is unknown; no process was changed.",
            )
        result = WebStatus(installed=record is not None, loaded=loaded, ready=False, pid=pid)
        if record is not None:
            result.port = int(str(record["port"]))
            if os.path.lexists(LauncherTransition(self.directory, self.prefix, self.executable).path):
                result.problem = "The web launcher transition requires its original install or uninstall command."
                return result
            if self.historical_record(record):
                result.problem = "The owned web launcher needs migration. Run `hz ui install` with its recorded port."
                return result
        if record is None or pid is None:
            if installation_mutation_pending(self.prefix, self.directory.parent):
                result.problem = (
                    "An installation change requires completion or recovery before this web service can start. "
                    "`hz system daemon restart` recovers one whose process provably ended, for example after a "
                    "restart of the computer; the service then starts by itself."
                )
            return result
        try:
            live = self._control(record, pid, "status")
            version = live.get("version")
            if not isinstance(version, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}", version) is None:
                return result
            result.ready, result.instance, result.version = True, str(live["instance"]), str(live["version"])
        except (OSError, ValueError, HeinzelError):
            result.problem = "The owned job has not reported a matching ready web server."
        return result

    def install(self, port: int, *, dry_run: bool = False) -> WebChange:
        """Install the web service under one exclusive lifecycle writer.

        Raises:
            HeinzelError: If installation or writer ownership cannot be established.
        """
        if dry_run:
            return self._install(port, dry_run=True)
        if self._transition_needed():
            with MaintenanceLocks((host_maintenance_lock(self.prefix), self.directory / "lifecycle.lock")) as locks:
                return self._transition("migrate", locks=locks, port=port)
        with private_lock(self.directory / "lifecycle.lock"):
            return self._install(port)

    def _install(self, port: int, *, dry_run: bool = False) -> WebChange:
        """Install only into an absent owned label and free fixed port.

        Raises:
            HeinzelError: If ownership, user session, installation or load cannot be verified.
            OSError: If the port is occupied or writing fails.
        """
        if not 1 <= port <= MAX_PORT:
            msg = "Use a fixed web port between 1 and 65535."
            raise HeinzelError(msg)
        profile = validated_profile(self.profile, self.home, self.directory.parent, self.prefix, self.executable)
        require_installation_profile(profile, self.unsupported)
        existing = self._record()
        loaded, _ = self._manager(existing)
        if existing is not None:
            return self._existing_install(existing, port, dry_run=dry_run)
        if any(path.exists() or path.is_symlink() for path in (self.marker, self.unit, self.launcher)) or loaded:
            msg = "Existing web integration is not owned; it was left unchanged."
            raise HeinzelError(msg)
        generation = secrets.token_hex(16)
        content = self._content()
        if dry_run:
            return WebChange(
                changed=False,
                dry_run=True,
                files={str(self.unit): content, str(self.launcher): self._launch_content(port, generation)},
                status=WebStatus(installed=False, loaded=False, ready=False),
            )
        self._prepare_install(port)
        private_write(self.launcher, self._launch_content(port, generation))
        descriptor = os.open(self.unit, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        private_write(
            self.marker,
            json.dumps(
                {
                    "port": port,
                    "generation": generation,
                    "executable": str(self.executable),
                    "prefix": str(self.prefix),
                    "uv": str(self.uv),
                    "profile": self.profile,
                    "unit": str(self.unit),
                    "content": content,
                    "launcher": self._launch_content(port, generation),
                }
            ),
        )
        commands = (
            [self._command("bootstrap", f"gui/{self.uid}", str(self.unit))]
            if self.platform == "darwin"
            else [self._command("daemon-reload"), self._command("enable", "--now", UNIT)]
        )
        for command in commands:
            if self.runner(command).returncode:
                msg = (
                    "The web integration was written but could not be loaded. "
                    "Inspect `hz ui status`; no readiness is claimed."
                )
                raise HeinzelError(msg)
        status = self.status()
        if not status.loaded:
            msg = "The service manager did not confirm the exact web integration. Inspect `hz ui status`."
            raise HeinzelError(msg)
        return WebChange(
            changed=True,
            files={str(self.unit): content, str(self.launcher): self._launch_content(port, generation)},
            status=status,
        )

    def _existing_install(self, existing: dict[str, JsonValue], port: int, *, dry_run: bool) -> WebChange:
        if existing["port"] != port:
            msg = "Uninstall the existing owned web service before changing its port."
            raise HeinzelError(msg)
        if self.historical_record(existing):
            if not dry_run:
                msg = "The owned launcher changed before migration; retry `hz ui install` with its recorded port."
                raise HeinzelError(msg)
            selected = validated_profile(
                existing["profile"], self.home, self.directory.parent, self.prefix, self.executable
            )
            proposal = self._launch_content(port, str(existing["generation"]), selected)
            return WebChange(changed=False, dry_run=True, files={str(self.launcher): proposal}, status=self.status())
        return WebChange(changed=False, dry_run=dry_run, status=self.status())

    def _transition_needed(self) -> bool:
        return os.path.lexists(
            LauncherTransition(self.directory, self.prefix, self.executable).path
        ) or self.historical_record(self._record())

    def _prepare_transition(
        self, action: Action, port: int | None, retiring_instance: str | None, locks: MaintenanceLocks
    ) -> LauncherTransition:
        if not self.verify() or not self.executable.is_file():
            msg = "Verify the selected standard Heinzel SDK before changing its historical web launcher."
            raise HeinzelError(msg)
        transition = LauncherTransition(self.directory, self.prefix, self.executable, locks)
        if os.path.lexists(transition.path):
            value = transition.load()
        else:
            old = self._record()
            if old is None or not self.historical_record(old):
                msg = "The historical web integration changed; no transition was started."
                raise HeinzelError(msg)
            if installation_mutation_pending(self.prefix, self.directory.parent):
                msg = "An existing installation change needs recovery before web launcher migration or retirement."
                raise HeinzelError(msg)
            profile = validated_profile(old["profile"], self.home, self.directory.parent, self.prefix, self.executable)
            require_installation_profile(profile, self.unsupported)
            if port is not None and old["port"] != port:
                msg = "Use the historical web service's recorded port for migration."
                raise HeinzelError(msg)
            self._manager(old)
            new = None
            if action == "migrate":
                new = old | {"generation": secrets.token_hex(16), "uv": str(self.uv)}
                new["launcher"] = self._launch_content(int(str(old["port"])), str(new["generation"]), profile)
                if len(json.dumps(new).encode()) > 16 * 1024:
                    msg = "The canonical web record exceeds its existing control-file budget; no job was stopped."
                    raise HeinzelError(msg)
            transition.prepare(
                action, (old, new), (self.unit, self.launcher, self.marker), retiring_instance=retiring_instance
            )
            value = transition.record
        transition.validate(
            action, (self.unit, self.launcher, self.marker), self.valid_record_value, self.historical_record
        )
        if port is not None and value.old["port"] != port:
            msg = "Use the historical web service's recorded port for migration."
            raise HeinzelError(msg)
        self._manager(value.old)
        return transition

    def _transition(
        self, action: Action, *, locks: MaintenanceLocks, port: int | None = None, retiring_instance: str | None = None
    ) -> WebChange:
        transition = self._prepare_transition(action, port, retiring_instance, locks)
        try:
            self._apply_transition(transition)
        except (OSError, ValueError, HeinzelError, subprocess.SubprocessError) as error:
            command = (
                f"hz ui install --port {transition.record.old['port']}" if action == "migrate" else "hz ui uninstall"
            )
            msg = (
                "The web launcher transition is incomplete; its exact recovery fences and user data were kept. "
                f"Retry `{command}`."
            )
            raise HeinzelError(msg) from error
        return WebChange(changed=True, status=self.status())

    def _apply_transition(self, transition: LauncherTransition) -> None:
        value = transition.record
        if value.phase in {"cleaned", "complete"}:
            transition.require_gates(terminal=True)
            transition.finish()
            return
        if value.phase == "prepared":
            if self._record() != value.old:
                msg = "The original web launcher changed before its first stop."
                raise HeinzelError(msg)
            transition.publish()
            self._stop_owned(value.old, fence=transition.require_gates)
            transition.require_gates()
            transition.phase("stopped")
        transition.require_gates()
        transition.check_files()
        if transition.record.phase == "stopped":
            self._replace_or_retire(transition)
        if transition.record.phase in {"canonical", "loaded"}:
            self._load_transition(transition)
        elif transition.record.phase == "retired":
            self._finish_retirement(transition)
        if not self.verify():
            msg = "The selected SDK changed during web launcher transition; its fences were kept."
            raise HeinzelError(msg)
        transition.finish()

    def _replace_or_retire(self, transition: LauncherTransition) -> None:
        value = transition.record
        if not self._stop_complete(value.old):
            msg = "The historical web job is not proven quiescent; its recovery fence remains."
            raise HeinzelError(msg)
        if value.kind == "web-launcher-migrate":
            transition.replace()
        else:
            transition.retire()

    def _finish_retirement(self, transition: LauncherTransition) -> None:
        value = transition.record
        transition.require_gates()
        if self.platform == "linux" and self.runner(self._command("daemon-reload")).returncode:
            msg = "The retired web definitions need a verified user-manager reload."
            raise HeinzelError(msg)
        if not self._stop_complete(value.old):
            msg = "The retired web job is not proven stopped; its recovery fence remains."
            raise HeinzelError(msg)

    def _load_transition(self, transition: LauncherTransition) -> None:
        new = transition.record.new
        if new is None or self._record() != new:
            msg = "The canonical web launcher is not coherent; its recovery fence remains."
            raise HeinzelError(msg)
        transition.require_gates()
        loaded, _ = self._manager(new)
        if transition.record.phase != "loaded" or not loaded:
            self._stop_owned(new, fence=transition.require_gates)
            self._prepare_install(int(str(new["port"])))
            commands = (
                [self._command("bootstrap", f"gui/{self.uid}", str(self.unit))]
                if self.platform == "darwin"
                else [self._command("daemon-reload"), self._command("enable", "--now", UNIT)]
            )
            for command in commands:
                transition.require_gates()
                if self.runner(command).returncode:
                    msg = "The canonical web launcher could not be loaded; retry its explicit migration command."
                    raise HeinzelError(msg)
            if not self._manager(new)[0]:
                msg = "The manager did not confirm the canonical launcher; its recovery fence remains."
                raise HeinzelError(msg)
            transition.phase("loaded")

    def _prepare_install(self, port: int) -> None:
        if not self.verify() or not self.executable.is_file():
            msg = "Install and verify the persistent uv tool app before enabling its web service."
            raise HeinzelError(msg)
        if self.platform == "darwin":
            session = self.runner(self._command("managername"))
            owner = self.runner(self._command("manageruid"))
            if session.returncode or session.stdout.strip() != "Aqua" or owner.stdout.strip() != str(self.uid):
                msg = "Install web availability from your own logged-in desktop session."
                raise HeinzelError(msg)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
        private_directory(self.directory)
        self.unit.parent.mkdir(parents=True, exist_ok=True)
        # Unit parents are shared configuration, but every physical component must be owned and non-writable by others.
        for parent in (self.unit.parent, *self.unit.parent.parents):
            info = parent.lstat()
            sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid not in {0, self.uid}
                or (info.st_mode & 0o022 and not sticky_root)
            ):
                msg = "The service definition directory has unsafe ownership or links."
                raise HeinzelError(msg)

    def open_local(self) -> str | None:
        """Return a fresh session link only to the explicit local Open action.

        Raises:
            HeinzelError: If owned integration exists but its runtime cannot be verified.
        """
        try:
            self.marker.lstat()
        except FileNotFoundError:
            return None
        record = self._record()
        if self._transition_needed():
            msg = "Complete the owned launcher migration with `hz ui install` before opening this service."
            raise HeinzelError(msg)
        loaded, pid = self._manager(record)
        if record is None or not loaded or pid is None:
            msg = "The installed web service is not ready. Inspect `hz ui status`."
            raise HeinzelError(msg)
        response = self._control(record, pid, "open")
        url = response.get("url")
        if (
            not isinstance(url, str)
            or re.fullmatch(rf"http://127\.0\.0\.1:{record['port']}/#token=[A-Za-z0-9_-]{{43}}", url) is None
        ):
            msg = "The owned web service did not supply a valid local session."
            raise HeinzelError(msg)
        return url

    def restart(self) -> WebRestart:
        """Ask the live owned web server to use its existing socket and token handoff.

        Raises:
            HeinzelError: If exact ownership or readiness cannot be established.
        """
        if self._transition_needed():
            with MaintenanceLocks((host_maintenance_lock(self.prefix), self.directory / "lifecycle.lock")) as locks:
                transition = LauncherTransition(self.directory, self.prefix, self.executable)
                if os.path.lexists(transition.path):
                    prior = transition.load()
                    instance = prior.retiring_instance
                    if instance is None:
                        msg = "Complete the pending launcher operation with its original install or uninstall command."
                        raise HeinzelError(msg)
                else:
                    record = self._record()
                    loaded, pid = self._manager(record)
                    if record is None or not loaded or pid is None:
                        msg = "No owned running historical service can restart; use `hz ui install` to migrate it."
                        raise HeinzelError(msg)
                    instance = str(self._control(record, pid, "status")["instance"])
                self._transition("migrate", locks=locks, retiring_instance=instance)
                return WebRestart(
                    requested=True,
                    retiring_instance=instance,
                    message="Launcher migrated and restart requested. Use `hz ui status` to verify the replacement.",
                )
        record = self._record()
        loaded, pid = self._manager(record)
        if record is None or not loaded or pid is None:
            msg = "No owned running web service can restart. Inspect `hz ui status`."
            raise HeinzelError(msg)
        if installation_mutation_pending(self.prefix, self.directory.parent):
            msg = "An installation change requires completion or recovery before restarting the web service."
            raise HeinzelError(msg)
        response = self._control(record, pid, "restart")
        return WebRestart(
            requested=True,
            retiring_instance=str(response["instance"]),
            message="Restart requested. Use `hz ui status` to verify a different ready instance.",
        )

    @contextmanager
    def removal_guard(self, *, locks: MaintenanceLocks | None = None) -> Generator[WebRemoval, None, None]:
        """Hold exact integration authority, separating stop from cleanup after verified tool removal.

        Yields:
            Captured integration files and separate existing verified stop/cleanup actions.

        Raises:
            HeinzelError: If files or the loaded user job are foreign, changed or unavailable.
        """
        path = self.directory / "lifecycle.lock"
        with locks.guard(path) if locks is not None else private_lock(path):
            if os.path.lexists(LauncherTransition(self.directory, self.prefix, self.executable).path):
                msg = "Complete the web launcher's explicit migration or retirement before removing the SDK."
                raise HeinzelError(msg)
            record = self._record()
            if not self._integration_absent() and record is None:
                msg = "The web service is foreign or changed; it was left unchanged."
                raise HeinzelError(msg)
            self._manager(record)
            files = tuple(owned_file(path) for path in (self.unit, self.launcher, self.marker)) if record else ()

            compatibility = (self.compatibility_gate(),) if self.historical_record(record) else ()

            def stop(token: str | None = None) -> None:
                self._removal_stop(record, compatibility, token)

            def finish() -> WebChange:
                if record and not self._stop_complete(record):
                    msg = "The web job is not stopped; its integration files were retained."
                    raise HeinzelError(msg)
                for captured in files:
                    remove_owned_file(captured)
                if files and self.platform == "linux" and self.runner(self._command("daemon-reload")).returncode:
                    msg = "Web integration was removed but the user manager needs a local reload."
                    raise HeinzelError(msg)
                return WebChange(changed=bool(files), status=WebStatus(installed=False, loaded=False, ready=False))

            manager_units: dict[str, dict[str, object]] = {}
            if self.platform == "linux" and files:
                manager_units[UNIT] = {
                    "fragment": str(self.unit),
                    "program": "/bin/sh",
                    "arguments": f"/bin/sh {self.launcher}",
                    "directory": str(self.home),
                    "disabled": True,
                }
            yield WebRemoval(files, stop, finish, manager_units, compatibility)

    def _removal_stop(
        self, record: dict[str, JsonValue] | None, compatibility: tuple[Path, ...], token: str | None
    ) -> None:
        def fence() -> None:
            if token is None or any(
                private_read(path) != json.dumps({"token": token}, indent=2) + "\n"
                for path in (*compatibility, host_maintenance_gate(self.prefix))
            ):
                msg = "The historical web launcher needs its exact paired fences before stopping."
                raise HeinzelError(msg)

        if compatibility:
            fence()
        if record:
            self._stop_owned(record, fence=fence if token is not None else None)

    def uninstall(self) -> WebChange:
        """Remove exact owned integration under one exclusive lifecycle writer.

        Raises:
            HeinzelError: If loaded or filesystem ownership cannot be established.
        """
        if self._transition_needed():
            with MaintenanceLocks((host_maintenance_lock(self.prefix), self.directory / "lifecycle.lock")) as locks:
                return self._transition("retire", locks=locks)
        with private_lock(self.directory / "lifecycle.lock"):
            return self._uninstall()

    def _integration_absent(self) -> bool:
        for path in (self.marker, self.unit, self.launcher):
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            return False
        return True

    def _stop_complete(self, record: dict[str, JsonValue]) -> bool:
        """Observe exact owned identity on every stop sample; foreign or unknown evidence fails immediately.

        Raises:
            HeinzelError: If the manager identity or stop state cannot be verified.
        """
        loaded, _ = self._manager(record)
        if self.platform == "darwin":
            return not loaded
        stopped = self.runner(self._command("show", UNIT, "--property=ActiveState,MainPID"))
        properties = {
            key: value for line in stopped.stdout.splitlines() if "=" in line for key, value in [line.split("=", 1)]
        }
        if not stopped.returncode and properties == {"ActiveState": "inactive", "MainPID": "0"}:
            return True
        pid = properties.get("MainPID", "")
        if (
            stopped.returncode
            or not loaded
            or set(properties) != {"ActiveState", "MainPID"}
            or properties["ActiveState"] not in {"active", "activating", "deactivating", "reloading"}
            or not pid.isascii()
            or not pid.isdecimal()
        ):
            msg = "The web service stop could not be verified; its files were retained."
            raise HeinzelError(msg)
        return False

    def _verify_stopped(self, record: dict[str, JsonValue]) -> None:
        """Wait boundedly for an already authorized stop, preserving every ownership check and integration file.

        Raises:
            HeinzelError: If ownership changes, evidence is unknown, or the exact owned job does not settle.
        """
        deadline = self._clock() + STOP_SECONDS
        while True:
            if self._record() != record:
                msg = "Web integration changed during removal; its files were retained."
                raise HeinzelError(msg)
            if self._stop_complete(record):
                return
            remaining = deadline - self._clock()
            if remaining <= 0:
                msg = (
                    "The web job remains loaded; its files were retained."
                    if self.platform == "darwin"
                    else "The web service stop could not be verified; its files were retained."
                )
                raise HeinzelError(msg)
            self._sleep(min(STOP_POLL_SECONDS, remaining))

    def _uninstall(self) -> WebChange:
        """Unload only the exact owned OS service before removing its definition, preserving user data.

        Raises:
            HeinzelError: If integration or loaded identity is foreign, edited or unknown, or unloading fails.
        """
        if self._integration_absent():
            return WebChange(changed=False, status=WebStatus(installed=False, loaded=False, ready=False))
        record = self._record()
        if record is None:
            msg = "The web service is unowned or changed; nothing was removed."
            raise HeinzelError(msg)
        self._stop_owned(record)
        self.unit.unlink()
        self.launcher.unlink()
        self.marker.unlink()
        if self.platform == "linux" and self.runner(self._command("daemon-reload")).returncode:
            msg = "Web integration was removed but systemd reload failed; inspect your user manager."
            raise HeinzelError(msg)
        return WebChange(changed=True, status=self.status())

    def _stop_owned(self, record: dict[str, JsonValue], *, fence: Callable[[], None] | None = None) -> None:
        """Stop only the exact loaded integration, preserving its files until removal is confirmed.

        Raises:
            HeinzelError: If loaded ownership or the stopped state cannot be verified.
        """
        loaded, _ = self._manager(record)
        if loaded:
            command = (
                self._command("bootout", f"gui/{self.uid}/{LABEL}")
                if self.platform == "darwin"
                else self._command("disable", "--now", UNIT)
            )
            if fence is not None:
                fence()
            if self.runner(command).returncode:
                msg = "The owned web service could not be unloaded; its files were retained."
                raise HeinzelError(msg)
        self._verify_stopped(record)
        if self._record() != record:
            msg = "Web integration changed during removal; its files were retained."
            raise HeinzelError(msg)
