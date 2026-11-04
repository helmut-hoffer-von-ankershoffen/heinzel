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
from pathlib import Path

from pydantic import BaseModel, Field, JsonValue, TypeAdapter

from heinzel import Config, HeinzelError, Kernel, Settings
from heinzel.kernel import (
    Installation,
    PrivateControl,
    feature_enabled,
    installation_mutation_paths,
    installation_mutation_pending,
    private_directory,
    private_lock,
    private_read,
    private_socket_directory,
    private_write,
)
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
        selected_preview: bool = True,
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
        self.selected_preview = selected_preview
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
            HeinzelError: If uv is absent or its bin directory cannot be read.
        """
        config = Config.load(Path.home())
        settings = config.settings(Settings, "hz")
        if not settings.state_dir.is_absolute():
            msg = "Persistent web availability requires an absolute hz.state_dir in your user configuration."
            raise HeinzelError(msg)
        installation = kernel.resolve(Installation)
        uv = shutil.which("uv")
        if uv is None:
            msg = "Install uv before installing the persistent web service."
            raise HeinzelError(msg)
        result = _run([uv, "tool", "dir", "--bin"])
        binary = Path(result.stdout.strip())
        if result.returncode != 0 or not binary.is_absolute():
            msg = "The uv tool executable directory could not be verified."
            raise HeinzelError(msg)
        return cls(
            settings.state_dir,
            Path.home(),
            binary / "hz",
            prefix=installation.prefix.resolve(strict=True),
            uv=Path(uv).absolute(),
            profile=user_profile(
                config, settings, Path.home(), installation.prefix.resolve(strict=True), binary / "hz"
            ),
            unsupported=installation_overrides(config),
            selected_preview=feature_enabled(settings, "persistent_web"),
            verify=lambda: (
                installation.is_tool
                and installation.startup_state_matches()
                and (binary / "hz").samefile(installation.prefix / "bin/hz")
            ),
        )

    def _command(self, *arguments: str) -> list[str]:
        manager = "launchctl" if self.platform == "darwin" else "systemctl"
        executable = self.which(manager)
        if self.platform not in {"darwin", "linux"} or executable is None:
            msg = "Persistent web access requires macOS launchd or Linux systemd user services."
            raise HeinzelError(msg)
        return [executable, *arguments] if self.platform == "darwin" else [executable, "--user", *arguments]

    def _launch_content(self, port: int, generation: str, profile: dict[str, str] | None = None) -> str:
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

    def _record(self) -> dict[str, JsonValue] | None:
        try:
            value = TypeAdapter(dict[str, JsonValue]).validate_json(private_read(self.marker))
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
                return None
            profile = validated_profile(
                value.get("profile"), self.home, self.directory.parent, self.prefix, self.executable
            )
            port, generation = value.get("port"), value.get("generation")
            if (
                profile is None
                or type(port) is not int
                or not 1 <= port <= MAX_PORT
                or not isinstance(generation, str)
                or re.fullmatch(r"[0-9a-f]{32}", generation) is None
                or value["executable"] != str(self.executable)
                or value["prefix"] != str(self.prefix)
                or value["uv"] != str(self.uv)
                or value["unit"] != str(self.unit)
                or value["content"] != self._content()
                or value["launcher"] != self._launch_content(port, generation, profile)
                or private_read(self.launcher) != value["launcher"]
            ):
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
        if record is None or pid is None:
            if installation_mutation_pending(self.prefix, self.directory.parent):
                result.problem = (
                    "An installation change requires completion or recovery before this web service can start."
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
        if not self.selected_preview:
            msg = "Enable persistent_web in the selected user configuration before installing web availability."
            raise HeinzelError(msg)
        existing = self._record()
        loaded, _ = self._manager(existing)
        if existing is not None:
            if existing["port"] != port:
                msg = "Uninstall the existing owned web service before changing its port."
                raise HeinzelError(msg)
            return WebChange(changed=False, dry_run=dry_run, status=self.status())
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
    def removal_guard(self) -> Generator[Callable[[], WebChange], None, None]:
        """Inspect all owned integration before another removal mutates it; retain the lifecycle lock.

        Yields:
            The existing verified remover, including when the preview flag is disabled.

        Raises:
            HeinzelError: If the filesystem or loaded job is foreign or changed.
        """
        with private_lock(self.directory / "lifecycle.lock"):
            record = self._record()
            if not self._integration_absent() and record is None:
                msg = "The web service is foreign or changed; it was left unchanged."
                raise HeinzelError(msg)
            self._manager(record)  # Even absent files cannot authorize removal of an unowned loaded label.
            yield self._uninstall

    def uninstall(self) -> WebChange:
        """Remove exact owned integration under one exclusive lifecycle writer.

        Raises:
            HeinzelError: If loaded or filesystem ownership cannot be established.
        """
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
        loaded, _ = self._manager(record)
        if loaded:
            command = (
                self._command("bootout", f"gui/{self.uid}/{LABEL}")
                if self.platform == "darwin"
                else self._command("disable", "--now", UNIT)
            )
            if self.runner(command).returncode:
                msg = "The owned web service could not be unloaded; its files were retained."
                raise HeinzelError(msg)
        self._verify_stopped(record)
        if self._record() != record:
            msg = "Web integration changed during removal; its files were retained."
            raise HeinzelError(msg)
        self.unit.unlink()
        self.launcher.unlink()
        self.marker.unlink()
        if self.platform == "linux" and self.runner(self._command("daemon-reload")).returncode:
            msg = "Web integration was removed but systemd reload failed; inspect your user manager."
            raise HeinzelError(msg)
        return WebChange(changed=True, status=self.status())
