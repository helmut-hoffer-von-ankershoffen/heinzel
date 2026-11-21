"""Bounded launchctl and systemctl children and the parsers of what they print."""

import os
import re
import selectors
import stat
import subprocess
import time
from contextlib import suppress
from pathlib import Path

from heinzel.kernel import ProcessOutcome
from heinzel.modules.system.daemon._units import LABEL

MAX_SERVICE_MANAGER_OUTPUT = 1_000_000
MANAGER_TIMEOUT = 5
# launchd can defer a respawn for ten seconds (launchd.plist(5)); allow time for its reply as well.
OWNED_KICKSTART_TIMEOUT = 15


def _launchctl_quiescent_output(output: str) -> bool:
    """Whether the documented legacy list format holds no daemon record."""
    labels = _launchctl_records(output)
    return labels is not None and LABEL not in labels


def _launchctl_records(output: str) -> dict[str, str] | None:
    """Each label's PID column from the documented legacy list format; any unexpected record fails closed."""
    if len(output.encode("utf-8")) > MAX_SERVICE_MANAGER_OUTPUT:
        return None
    lines = output.splitlines()
    if not lines or lines[0].split() != ["PID", "Status", "Label"]:
        return None
    labels: dict[str, str] = {}
    expected_columns = 3
    for line in lines[1:]:
        parts = line.split()
        if (
            len(parts) != expected_columns
            or (parts[0] != "-" and not parts[0].isdigit())
            or not re.fullmatch(r"-?[0-9]+", parts[1])
            or parts[2] in labels
        ):
            return None
        labels[parts[2]] = parts[0]
    return labels


def _manager_environment(command: list[str]) -> dict[str, str]:
    if command[0] != "/usr/bin/systemctl":
        return {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(Path.home())}
    runtime = f"/run/user/{os.getuid()}"
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "XDG_RUNTIME_DIR": runtime,
    }


def _manager_deadline(command: list[str]) -> float:
    own_kickstart = command == ["/bin/launchctl", "kickstart", f"gui/{os.getuid()}/{LABEL}"]
    return time.monotonic() + (OWNED_KICKSTART_TIMEOUT if own_kickstart else MANAGER_TIMEOUT)


def bounded_manager_command(
    command: list[str], *, deadline: float | None = None, env: dict[str, str] | None = None
) -> ProcessOutcome:
    """Bound a manager child; never signal a reaped or unknown process."""
    process = subprocess.Popen(  # noqa: S603 - fixed absolute launchctl and fixed subcommands
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env if env is not None else _manager_environment(command),
    )
    deadline = deadline if deadline is not None else _manager_deadline(command)
    captured = bytearray()
    truncated = False
    try:
        if process.stdout is None:
            return ProcessOutcome(None, "", "")
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                events = selector.select(max(0, deadline - time.monotonic()))
                if not events:
                    break
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                captured.extend(chunk)
                if len(captured) > MAX_SERVICE_MANAGER_OUTPUT:
                    truncated = True
                    break
        if truncated or time.monotonic() >= deadline:
            return ProcessOutcome(None, "", "", truncated=truncated)
        try:
            status = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            return ProcessOutcome(None, "", "")
        return ProcessOutcome(status, captured.decode("utf-8"), "")
    finally:
        if process.poll() is None:
            with suppress(ProcessLookupError, PermissionError):
                process.kill()
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=1)
        if process.stdout is not None:
            process.stdout.close()


# Retain the internal lifecycle probe name used by the Linux session acceptance harness.
_quiescence_command = bounded_manager_command


def _linux_user_manager_identity(run_root: Path = Path("/run")) -> tuple[int, ...] | None:
    """Observe the private manager socket and its physical, safely owned parent directories."""
    try:
        uid = os.getuid()
        parents = [run_root, run_root / "user"]
        runtime = run_root / "user" / str(uid)
        for path in parents:
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                return None
        directory = runtime.lstat()
        systemd = (runtime / "systemd").lstat()
        socket = (runtime / "systemd/private").lstat()
        if (
            not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != uid
            or directory.st_mode & 0o022
            or not stat.S_ISDIR(systemd.st_mode)
            or systemd.st_uid != uid
            or systemd.st_mode & 0o022
            or not stat.S_ISSOCK(socket.st_mode)
            or socket.st_uid != uid
        ):
            return None
    except OSError:
        return None
    else:
        return directory.st_dev, directory.st_ino, systemd.st_dev, systemd.st_ino, socket.st_dev, socket.st_ino


def _systemd_quiescent_unit(output: str, name: str) -> bool:
    """Require the documented unit properties, including an empty queued-job property."""
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in values:
            return False
        values[key] = value
    required = {"Id", "LoadState", "ActiveState", "SubState", "Job"}
    if not required.issubset(values) or set(values) - required - {"MainPID", "ControlPID"}:
        return False
    if (
        values["Id"] != name
        or values["LoadState"] not in {"loaded", "not-found"}
        or values["ActiveState"] != "inactive"
        or values["SubState"] != "dead"
        or values["Job"] != ""
    ):
        return False
    if name.endswith(".service") and values["LoadState"] == "loaded":
        return values.get("MainPID") == values.get("ControlPID") == "0"
    return all(values.get(key, "0") == "0" for key in ("MainPID", "ControlPID"))


def launchctl_records(output: str) -> dict[str, str] | None:
    """Parse the same complete service-manager listing used by normal lifecycle checks."""
    return _launchctl_records(output)
