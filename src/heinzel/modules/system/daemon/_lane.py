"""The scheduled tick's calendar reminder lane, seen from the tick: start, hand over, supervise (ADR 0147).

The tick starts the lane before its jobs, closes the lane's pipe once its jobs are done and its daemon locks are
released, then waits for the lane while holding no daemon lock. A lane that crashes is restarted with backoff, at
most three times per tick; a lane whose teardown crossed its slot is followed by a new lane for the next window, so a
dropped firing never leaves an interval without reminders.
"""

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, cast

from heinzel.modules.system.daemon import _process_birth
from heinzel.modules.system.daemon._runtime import write_private

ENVIRONMENT = "HZ_REMINDER_LANE"
COMMAND = ("org", "calendar-reminder-lane")
BACKOFF = (5.0, 15.0, 45.0)
"""Seconds before each restart of a crashed lane; after the third restart a crash ends the lane until the next tick."""
SLOT_EPSILON = timedelta(seconds=1)
LANE_FILE = "lane.json"
MARKER = "lane-marker.json"


class Child(Protocol):
    """A started lane process."""

    pid: int

    def wait(self) -> int:
        """Wait for the process to exit; its exit status."""
        ...

    def terminate(self) -> None:
        """Ask the process to end."""
        ...


def spawn(command: list[str], environment: Mapping[str, str], fd: int) -> Child:
    """Start the lane in the tick's process group, so the service manager stops it with the tick."""
    return subprocess.Popen(  # noqa: S603 - the verified shim and fixed arguments
        command,
        env=dict(environment),
        pass_fds=(fd,),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        close_fds=True,
    )


@dataclass
class LaneSupervisor:
    """One tick's lane: started before the jobs, released after them, restarted on a crash or a late teardown."""

    directory: Path
    command: list[str]
    interval: int
    environment: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    start_process: Callable[[list[str], Mapping[str, str], int], Child] = spawn
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    sleep: Callable[[float], None] = time.sleep
    birth: Callable[[int], dict[str, int] | None] = lambda pid: _process_birth.process_birth(sys.platform, pid)
    child: Child | None = None
    offset: float = 0.0
    """Seconds the next tick starts after its slot (systemd's fixed delay); the lane delivers that much earlier."""
    crashes: int = 0
    restarts: int = 0
    overrun: str | None = None
    _writer: int | None = None
    _done: bool = False

    def start(self) -> None:
        """Start a lane; its pipe stays open until `jobs_done`."""
        reader, writer = os.pipe()
        try:
            os.set_inheritable(reader, True)  # noqa: FBT003 - os API
            spec = {
                "fd": reader,
                "dir": str(self.directory),
                "interval": self.interval,
                "offset": self.offset,
                "spawned": self.clock().timestamp(),
                "crashes": self.crashes,
                "restarts": self.restarts,
                "overrun": self.overrun,
            }
            environment = {**self.environment, ENVIRONMENT: json.dumps(spec, sort_keys=True)}
            self.child = self.start_process(self.command, environment, reader)
        except BaseException:
            os.close(writer)
            raise
        finally:
            os.close(reader)
        self._writer = writer
        if self._done:
            self._close()
        marker: dict[str, object] = {
            "parent": os.getpid(),
            "child": self.child.pid,
            "started": self.clock().isoformat(),
        }
        if (birth := self.birth(self.child.pid)) is not None:
            marker["birth"] = birth
        write_private(self.directory / MARKER, json.dumps(marker, sort_keys=True) + "\n")

    def jobs_done(self) -> None:
        """Tell the lane the tick's jobs are done, by closing its pipe."""
        self._done = True
        self._close()

    def stop(self) -> None:
        """End the lane at once: the tick failed before its jobs finished."""
        self.jobs_done()
        if self.child is not None:
            self.child.terminate()
            self.child.wait()
        (self.directory / MARKER).unlink(missing_ok=True)

    def finish(self) -> str:
        """Wait for the lane; restart it after a crash or a teardown past its slot. How the lane ended."""
        self.jobs_done()
        try:
            while self.child is not None:
                code = self.child.wait()
                if (self.directory / "paused").exists():
                    return "paused"  # a paused daemon pauses its reminders: never restart the lane
                if code != 0:
                    self.crashes += 1
                    self._record(f"crashed with {code}")
                    if self.restarts >= len(BACKOFF):
                        return "overdue"
                    self.sleep(BACKOFF[self.restarts])
                    self.restarts += 1
                    self.start()
                    continue
                beat = self._beat()
                if beat.get("pid") != self.child.pid:
                    return "duplicate"  # another lane holds the lock and wrote this heartbeat; never spin on it
                slot = _instant(beat.get("slot"))
                if beat.get("result") == "window" and slot is not None and self.clock() >= slot - SLOT_EPSILON:
                    self.overrun = self.clock().isoformat()
                    self._record("overrun")
                    self.start()
                    continue
                return str(beat.get("result") or "ended")
            return "ended"
        finally:
            (self.directory / MARKER).unlink(missing_ok=True)

    def _close(self) -> None:
        if self._writer is not None:
            os.close(self._writer)
            self._writer = None

    def _beat(self) -> dict[str, object]:
        try:
            data = json.loads((self.directory / LANE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return cast("dict[str, object]", data) if isinstance(data, dict) else {}

    def _record(self, result: str) -> None:
        """Add the crash and overrun counts to the heartbeat; only while no lane runs."""
        beat = self._beat() | {
            "result": result,
            "crashes": self.crashes,
            "restarts": self.restarts,
            "overrun": self.overrun,
            "at": self.clock().astimezone(UTC).isoformat(),
        }
        write_private(self.directory / LANE_FILE, json.dumps(beat, indent=2, sort_keys=True) + "\n")


def _instant(value: object) -> datetime | None:
    try:
        instant = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return instant if instant.tzinfo is not None else None


def stop_lane(
    directory: Path,
    birth: Callable[[int], dict[str, int] | None] = lambda pid: _process_birth.process_birth(sys.platform, pid),
    kill: Callable[[int, int], None] = os.kill,
) -> bool:
    """Send SIGTERM to the lane named in the marker, only if its process birth still matches (ADR 0147, point 8).

    A reused PID or a marker without a birth is never signalled. True when the lane was asked to end.
    """
    try:
        marker = json.loads((directory / MARKER).read_text(encoding="utf-8"))
        child, recorded = marker["child"], marker["birth"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if type(child) is not int or child <= 1 or not isinstance(recorded, dict) or birth(child) != recorded:
        return False
    try:
        kill(child, signal.SIGTERM)
    except ProcessLookupError:
        return False
    return True
