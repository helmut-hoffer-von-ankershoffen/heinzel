"""The tick's side of the calendar reminder lane: start, release, restart (ADR 0147)."""

import json
import os
import signal
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from heinzel.modules.system.daemon import _lane

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 13, 10, 0, 5, tzinfo=UTC)


class Child:
    def __init__(self, code: int, directory, beat=None) -> None:
        self.pid = 4242
        self.code = code
        self.directory = directory
        self.beat = beat
        self.terminated = False

    def wait(self) -> int:
        if self.beat is not None:
            (self.directory / _lane.LANE_FILE).write_text(json.dumps(self.beat))
        return self.code

    def terminate(self) -> None:
        self.terminated = True


class Spawner:
    """Hands out scripted children and records each start's specification."""

    def __init__(self, children) -> None:
        self.children = list(children)
        self.specs: list[dict] = []

    def __call__(self, command, environment, fd):
        spec = json.loads(environment[_lane.ENVIRONMENT])
        assert spec["fd"] == fd
        self.specs.append(spec)
        return self.children.pop(0)


def supervisor(tmp_path, children, clock=lambda: T0):
    sleeps: list[float] = []
    spawner = Spawner(children)
    lane = _lane.LaneSupervisor(
        tmp_path, ["hz", *_lane.COMMAND], 5, environment={}, start_process=spawner, clock=clock, sleep=sleeps.append
    )
    return lane, spawner, sleeps


def window(slot: datetime) -> dict:
    return {"result": "window", "slot": slot.isoformat(), "profile": "p", "pid": 4242}


def test_a_lane_that_ends_in_its_window_ends_the_tick_and_clears_its_marker(tmp_path):
    lane, spawner, sleeps = supervisor(tmp_path, [Child(0, tmp_path, window(T0 + timedelta(minutes=5)))])
    lane.start()
    marker = json.loads((tmp_path / _lane.MARKER).read_text())
    assert (marker["parent"], marker["child"]) == (os.getpid(), 4242)
    assert spawner.specs[0]["interval"] == 5
    assert lane.finish() == "window"
    assert not (tmp_path / _lane.MARKER).exists()
    assert sleeps == []


def test_a_crashed_lane_restarts_three_times_with_backoff_then_is_overdue(tmp_path):
    lane, spawner, sleeps = supervisor(tmp_path, [Child(-9, tmp_path) for _ in range(4)])
    lane.start()
    assert lane.finish() == "overdue"
    assert sleeps == [5.0, 15.0, 45.0]
    assert [spec["restarts"] for spec in spawner.specs] == [0, 1, 2, 3]
    beat = json.loads((tmp_path / _lane.LANE_FILE).read_text())
    assert (beat["crashes"], beat["restarts"], beat["result"]) == (4, 3, "crashed with -9")


def test_a_crash_then_a_clean_end_stops_restarting(tmp_path):
    lane, _, sleeps = supervisor(tmp_path, [Child(1, tmp_path), Child(0, tmp_path, window(T0 + timedelta(minutes=5)))])
    lane.start()
    assert lane.finish() == "window"
    assert sleeps == [5.0]


def test_a_teardown_past_the_slot_starts_a_lane_for_the_next_window(tmp_path):
    now = [T0]
    late = Child(0, tmp_path, window(T0 + timedelta(seconds=0.5)))
    lane, spawner, _ = supervisor(
        tmp_path, [late, Child(0, tmp_path, window(T0 + timedelta(minutes=5)))], clock=lambda: now[0]
    )
    lane.start()
    assert lane.finish() == "window"
    assert len(spawner.specs) == 2
    assert spawner.specs[1]["overrun"] == T0.isoformat()


def test_a_tick_that_fails_during_its_jobs_stops_its_lane(tmp_path):
    child = Child(0, tmp_path)
    lane, _, _ = supervisor(tmp_path, [child])
    lane.start()
    lane.stop()
    assert child.terminated
    assert not (tmp_path / _lane.MARKER).exists()


def test_a_lane_started_after_the_jobs_gets_a_closed_pipe(tmp_path):
    seen = []

    def start_process(command, environment, fd):
        seen.append(os.dup(fd))
        return Child(0, tmp_path)

    lane = _lane.LaneSupervisor(tmp_path, ["hz"], 5, environment={}, start_process=start_process, clock=lambda: T0)
    lane.jobs_done()
    lane.start()
    try:
        assert os.read(seen[0], 1) == b""  # end of file: the jobs are done
    finally:
        os.close(seen[0])


def test_a_failed_start_closes_both_ends_of_the_pipe(tmp_path):
    def broken(*_args):
        msg = "no shim"
        raise OSError(msg)

    descriptors = Path("/dev/fd")
    before = len(list(descriptors.iterdir()))
    lane = _lane.LaneSupervisor(tmp_path, ["hz"], 5, environment={}, start_process=broken, clock=lambda: T0)
    with pytest.raises(OSError, match="no shim"):
        lane.start()
    assert len(list(descriptors.iterdir())) == before
    assert lane.finish() == "ended"


def test_an_unreadable_heartbeat_ends_the_lane_quietly(tmp_path):
    child = Child(0, tmp_path)
    (tmp_path / _lane.LANE_FILE).write_text("[]")
    lane, _, _ = supervisor(tmp_path, [child])
    lane.start()
    assert lane.finish() == "duplicate"  # no heartbeat of its own: never restarted
    assert _lane._instant("not a time") is None
    assert _lane._instant("2026-11-13T10:00:00") is None


def test_stopping_a_lane_that_never_started_only_clears_its_marker(tmp_path):
    lane, _, _ = supervisor(tmp_path, [])
    (tmp_path / _lane.MARKER).write_text("{}")
    lane.stop()
    assert not (tmp_path / _lane.MARKER).exists()


def test_the_marker_records_the_lane_birth_and_only_a_matching_lane_is_stopped(tmp_path):
    lane, _, _ = supervisor(tmp_path, [Child(0, tmp_path)])
    lane.birth = lambda pid: {"ticks": pid, "hz": 0, "ns_dev": 0, "ns_ino": 0}
    lane.start()
    assert json.loads((tmp_path / _lane.MARKER).read_text())["birth"]["ticks"] == 4242
    killed: list[tuple[int, int]] = []

    def kill(pid, signum):
        killed.append((pid, signum))

    assert _lane.stop_lane(tmp_path, birth=lambda pid: {"ticks": pid, "hz": 0, "ns_dev": 0, "ns_ino": 0}, kill=kill)
    assert killed == [(4242, signal.SIGTERM)]
    assert not _lane.stop_lane(tmp_path, birth=lambda _pid: {"ticks": 1, "hz": 0, "ns_dev": 0, "ns_ino": 0}, kill=kill)
    assert len(killed) == 1  # a reused PID is never signalled

    def gone(_pid, _signum):
        raise ProcessLookupError

    assert not _lane.stop_lane(tmp_path, birth=lambda pid: {"ticks": pid, "hz": 0, "ns_dev": 0, "ns_ino": 0}, kill=gone)
    lane.finish()
    assert not _lane.stop_lane(tmp_path, kill=kill)  # no marker, nothing to stop


def test_a_marker_without_a_birth_is_never_signalled(tmp_path):
    (tmp_path / _lane.MARKER).write_text(json.dumps({"child": 4242}))
    assert not _lane.stop_lane(tmp_path, birth=lambda _pid: None, kill=lambda *_a: pytest.fail("signalled"))
    lane, _, _ = supervisor(tmp_path, [Child(0, tmp_path)])
    lane.birth = lambda _pid: None
    lane.start()
    assert "birth" not in json.loads((tmp_path / _lane.MARKER).read_text())


def test_the_supervisor_passes_the_timer_offset_to_the_lane(tmp_path):
    lane, spawner, _ = supervisor(tmp_path, [Child(0, tmp_path)])
    lane.offset = 42.0
    lane.start()
    assert spawner.specs[0]["offset"] == 42.0


def test_a_process_birth_of_this_process_matches_its_current_birth():
    import sys

    from heinzel.modules.system.daemon import _process_birth

    assert _process_birth.process_birth(sys.platform, os.getpid()) == _process_birth.current_birth(sys.platform)
    assert _process_birth.process_birth("win32", os.getpid()) is None


def test_a_paused_daemon_never_restarts_its_lane(tmp_path):
    lane, spawner, sleeps = supervisor(tmp_path, [Child(-9, tmp_path), Child(0, tmp_path)])
    lane.start()
    (tmp_path / "paused").write_text("")
    assert lane.finish() == "paused"
    assert (len(spawner.specs), sleeps) == (1, [])


def test_a_duplicate_lane_ends_supervision_instead_of_spinning(tmp_path):
    other = {"result": "window", "slot": T0.isoformat(), "profile": "p", "pid": 1}  # the older lane's, past its slot
    lane, spawner, _ = supervisor(tmp_path, [Child(0, tmp_path, other), Child(0, tmp_path, other)])
    lane.start()
    assert lane.finish() == "duplicate"
    assert len(spawner.specs) == 1
