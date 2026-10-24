import logging
import os
import stat
import subprocess
import threading
from datetime import UTC, datetime

import pytest

from heinzel import HeinzelError
from heinzel.modules.system.daemon import _runtime
from heinzel.modules.system.daemon._runtime import TickLog, describe, lock, private_dir, tail, within, write_private

pytestmark = pytest.mark.unit

AT = datetime(2026, 10, 5, 9, tzinfo=UTC)


def mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_within_returns_what_the_function_returned():
    outcome = within(lambda: {"draft": 1}, 1)
    assert (outcome.value, outcome.error, outcome.abandoned) == ({"draft": 1}, None, None)


def test_within_describes_failures():
    def fail() -> None:
        msg = "no feed"
        raise HeinzelError(msg)

    def bug() -> None:
        msg = "boom"
        raise RuntimeError(msg)

    assert within(fail, 1).error == "no feed"
    assert within(bug, 1).error == "RuntimeError: boom"


def test_within_abandons_a_function_that_overruns_and_says_so():
    release = threading.Event()
    outcome = within(release.wait, 0.01)
    assert outcome.error == ("timed out after 0.01s and was abandoned; it may still be running and have side effects")
    assert outcome.abandoned is not None
    assert outcome.abandoned.is_alive()
    release.set()
    outcome.abandoned.join()


def test_describe_marks_bugs_with_their_type():
    assert describe(ValueError("x")) == "ValueError: x"


def test_the_lock_is_exclusive_and_private(tmp_path):
    path = tmp_path / "state" / "tick.lock"
    with lock(path) as first, lock(path) as second:
        assert (first, second) == (True, False)
    with lock(path) as again:
        assert again
    assert (mode(path), mode(path.parent)) == (0o600, 0o700)


def test_run_turns_a_timeout_into_exit_code_124(monkeypatch):
    def hang(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", hang)
    assert _runtime.run(["launchctl", "print"]).returncode == 124


def test_write_private_is_atomic_and_private_whatever_the_umask(tmp_path):
    previous = os.umask(0)
    try:
        path = tmp_path / "a" / "b" / "state.json"
        write_private(path, "{}\n")
    finally:
        os.umask(previous)
    assert path.read_text(encoding="utf-8") == "{}\n"
    assert (mode(path), mode(path.parent), mode(path.parent.parent)) == (0o600, 0o700, 0o700)
    assert list(path.parent.iterdir()) == [path]  # no temporary file left behind


def test_write_private_can_leave_a_shared_directory_alone(tmp_path):
    shared = tmp_path / "LaunchAgents"
    shared.mkdir(mode=0o755)
    write_private(shared / "unit.plist", "x", shared_dir=True)
    assert (mode(shared / "unit.plist"), mode(shared)) == (0o600, 0o755)


def test_private_dir_tightens_an_existing_directory(tmp_path):
    loose = tmp_path / "logs"
    loose.mkdir(mode=0o755)
    assert mode(private_dir(loose)) == 0o700


def test_tick_log_writes_private_json_lines_that_tail_reads(tmp_path):
    path = tmp_path / "log" / "daemon.jsonl"
    times = iter([AT, AT.replace(minute=1)])
    log = TickLog(path, "t1", lambda: next(times))
    log.write(logging.INFO, "first", job="example:digest", error=None)
    log.write(logging.ERROR, "second", receipt="abc")
    log.close()
    assert mode(path) == 0o600
    path.with_name("daemon.jsonl.1").write_text(
        '{"at": "2026-10-05T08:00:00+00:00", "level": "INFO", "message": "old"}\n'
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
    lines = tail(path, 3)
    assert [(line.message, line.level, line.tick, line.job, line.receipt, line.at.minute) for line in lines] == [
        ("old", "INFO", None, None, None, 0),
        ("first", "INFO", "t1", "example:digest", None, 0),
        ("second", "ERROR", "t1", None, "abc", 1),  # each line has its own time
    ]
    assert [line.message for line in tail(path, 1)] == ["second"]  # the unreadable last line is left out


def test_rotated_logs_stay_private(tmp_path, monkeypatch):
    monkeypatch.setattr(_runtime, "LOG_BYTES", 100)
    path = tmp_path / "daemon.jsonl"
    log = TickLog(path, "t", lambda: AT)
    for n in range(5):
        log.write(logging.INFO, f"line {n} " + "x" * 50)
    log.close()
    assert {mode(p) for p in tmp_path.glob("daemon.jsonl*")} == {0o600}
    assert len(list(tmp_path.glob("daemon.jsonl*"))) > 1


def test_tail_of_a_missing_log_is_empty(tmp_path):
    assert tail(tmp_path / "daemon.jsonl", 5) == []


def test_closing_an_unused_log_is_fine(tmp_path):
    TickLog(tmp_path / "daemon.jsonl", "t", lambda: AT).close()
    assert not (tmp_path / "daemon.jsonl").exists()
