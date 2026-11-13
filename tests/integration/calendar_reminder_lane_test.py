"""The real `hz org calendar-reminder-lane` process under the tick's supervisor: start, crash, stop, restart."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from heinzel.modules.system.daemon import _lane

pytestmark = pytest.mark.integration

COMMAND = [sys.executable, "-m", "heinzel", *_lane.COMMAND]


def environment(tmp_path: Path) -> dict[str, str]:
    """This test's isolated HZ_* directories plus its own home; Calendar reminders on, nothing native."""
    return {**os.environ, "HOME": str(tmp_path / "home"), "HZ_SYSTEM_NOTIFY__CALENDAR": "true"}


def until(condition, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.1)


def beat(directory: Path) -> dict:
    try:
        return json.loads((directory / _lane.LANE_FILE).read_text())
    except (OSError, ValueError):
        return {}


def test_hand_started_lane_refuses(tmp_path):
    result = subprocess.run(
        COMMAND, env=environment(tmp_path), capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode != 0
    assert "Only a scheduled daemon tick starts the calendar reminder lane" in result.stdout + result.stderr


@pytest.mark.timeout(240)
def test_the_lane_survives_a_crash_stops_on_sigterm_and_a_restarted_daemon_gets_one_lane(tmp_path, monkeypatch):
    monkeypatch.setattr(_lane, "BACKOFF", (0.2, 0.2, 0.2))
    directory = tmp_path / "daemon"
    directory.mkdir()
    supervisor = _lane.LaneSupervisor(directory, COMMAND, 5, environment=environment(tmp_path))
    supervisor.start()
    first = supervisor.child
    assert first is not None
    marker = json.loads((directory / _lane.MARKER).read_text())
    assert (marker["parent"], marker["child"]) == (os.getpid(), first.pid)
    until(lambda: beat(directory).get("pid") == first.pid)
    assert beat(directory)["profile"]
    ended: list[str] = []
    finishing = threading.Thread(target=lambda: ended.append(supervisor.finish()))
    finishing.start()

    # A daemon restart while this lane runs: the new tick's lane finds the lane lock held and leaves at once.
    second = subprocess.run(
        COMMAND,
        env=environment(tmp_path)
        | {_lane.ENVIRONMENT: json.dumps({"fd": 0, "dir": str(directory), "interval": 5, "spawned": time.time()})},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert second.returncode == 0, second.stderr
    assert "duplicate" in second.stdout

    os.kill(first.pid, signal.SIGKILL)  # the lane crashes
    until(lambda: supervisor.child is not first and beat(directory).get("pid") == supervisor.child.pid)
    restarted = supervisor.child
    assert beat(directory)["restarts"] == 1
    os.kill(restarted.pid, signal.SIGTERM)  # an update stops the lane at once
    finishing.join(60)
    assert ended == ["stopped"]
    assert beat(directory)["result"] == "stopped"
    assert not (directory / _lane.MARKER).exists()
