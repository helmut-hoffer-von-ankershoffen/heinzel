"""Fast local scheduler observation for the interactive CLI."""

import os
import stat
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from heinzel.kernel import Config, DaemonOverview, LocalObservation, Settings
from heinzel.modules.system.daemon._models import DaemonSettings
from heinzel.modules.system.daemon._service import bounded_manager_command
from heinzel.modules.system.daemon._units import LABEL, SYSTEMD_NAME, plist_path, systemd_paths

MAX_HISTORY_BYTES = 4096
MAX_INTERVAL = 60


def _loaded(platform: str, deadline: float) -> bool | None:
    if platform == "darwin":
        command = ["/bin/launchctl", "list"]
    else:
        command = ["/usr/bin/systemctl", "--user", "is-active", "--quiet", f"{SYSTEMD_NAME}.timer"]
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C"}
    if platform == "linux":
        environment["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
    try:
        result = bounded_manager_command(command, deadline=deadline, env=environment)
    except OSError:
        return None
    if result.truncated:
        return None
    if platform == "darwin":
        lines = result.stdout.splitlines()
        if result.returncode != 0 or not lines or lines[0].split() != ["PID", "Status", "Label"]:
            return None
        return any(line.split()[-1:] == [LABEL] for line in lines[1:])
    return result.returncode == 0 if result.returncode in (0, 3) else None


def _unit_state(paths: tuple[Path, ...]) -> LocalObservation | None:
    try:
        present = 0
        for path in paths:
            try:
                path.stat()
            except FileNotFoundError:
                continue
            present += 1
    except OSError:
        return LocalObservation("unknown", "cannot inspect service files")
    if present == 0:
        return LocalObservation("not installed")
    if present != len(paths):
        return LocalObservation("unhealthy", "service installation incomplete")
    return None


def _history_bytes(state_dir: Path) -> bytes | None:
    """Read only an existing private regular heartbeat without following links or blocking."""
    directory = os.open(state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent = os.fstat(directory)
        if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
            return None
        descriptor = os.open("heartbeat.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
            ):
                return None
            return source.read(MAX_HISTORY_BYTES + 1)
    finally:
        os.close(directory)


def _history(state_dir: Path, now: datetime) -> tuple[str, bool]:  # noqa: PLR0911 - each invalid record is unknown
    """Read historical scheduled evidence, never as proof the scheduler is loaded."""
    try:
        data = _history_bytes(state_dir)
        if data is None:
            return "last scheduled tick unknown", False
        if len(data) > MAX_HISTORY_BYTES:
            return "last scheduled tick unknown", False
        record = TypeAdapter(dict[str, JsonValue]).validate_json(data)
        if record.get("source") != "scheduled":
            return "last scheduled tick unknown", False
        timestamp = record["completed_at"]
        if not isinstance(timestamp, str):
            return "last scheduled tick unknown", False
        completed = datetime.fromisoformat(timestamp)
        interval = record["interval_minutes"]
        outcome = record["outcome"]
        if (
            completed.tzinfo is None
            or not isinstance(interval, int)
            or isinstance(interval, bool)
            or not 1 <= interval <= MAX_INTERVAL
            or outcome not in ("completed", "failed")
        ):
            return "last scheduled tick unknown", False
        age = now - completed.astimezone(UTC)
        if age < timedelta(0):
            return "last scheduled tick unknown", False
        minutes_ago = int(age.total_seconds() // 60)
        if age > timedelta(minutes=interval * 2):
            return f"last scheduled tick stale ({minutes_ago}m ago; every {interval}m)", True
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        return "last scheduled tick unknown", False
    else:
        return f"last scheduled tick {outcome} ({minutes_ago}m ago; every {interval}m)", outcome == "failed"


def observe(
    unit_dir: Path, state_dir: Path, platform: str = sys.platform, *, deadline: float | None = None
) -> LocalObservation:
    """Read unit presence and historical tick state, with a bounded manager query."""
    if platform not in ("darwin", "linux"):
        return LocalObservation("unknown", "unsupported platform")
    paths = (plist_path(unit_dir),) if platform == "darwin" else systemd_paths(unit_dir)
    if issue := _unit_state(paths):
        return issue
    loaded = _loaded(platform, deadline if deadline is not None else time.monotonic() + 0.2)
    if loaded is None:
        return LocalObservation("unknown", "service manager unavailable")
    if not loaded:
        return LocalObservation("stopped", "scheduler not loaded")
    if (state_dir / "paused").exists():
        return LocalObservation("stopped", "scheduler loaded; jobs paused")
    history, unhealthy = _history(state_dir, datetime.now(UTC))
    return LocalObservation(
        "unhealthy" if unhealthy else "running", f"scheduler loaded; current tick unknown; {history}"
    )


class DaemonOverviewService(DaemonOverview):
    """Observe only the built-in scheduler using user-scope settings and no injectable plugin services."""

    def __init__(self) -> None:
        config = Config.load(Path.home())
        settings = config.settings(Settings, "hz")
        daemon = config.settings(DaemonSettings, "system.daemon")
        super().__init__(lambda deadline: observe(daemon.unit_dir, settings.state_dir / "daemon", deadline=deadline))
