import logging
import subprocess
import threading
from datetime import UTC, datetime

import pytest

from heinzel import HeinzelError
from heinzel.modules.system.daemon import _runtime
from heinzel.modules.system.daemon._runtime import TickLog, describe, lock, tail, within

pytestmark = pytest.mark.unit

AT = datetime(2026, 10, 5, 9, tzinfo=UTC)


def test_within_returns_none_when_the_function_succeeds():
    assert within(lambda: None, 1) is None


def test_within_describes_failures():
    def fail() -> None:
        msg = "no feed"
        raise HeinzelError(msg)

    def bug() -> None:
        msg = "boom"
        raise RuntimeError(msg)

    assert within(fail, 1) == "no feed"
    assert within(bug, 1) == "RuntimeError: boom"


def test_within_abandons_a_function_that_overruns():
    release = threading.Event()
    assert within(release.wait, 0.01) == "timed out after 0.01s"
    release.set()


def test_describe_marks_bugs_with_their_type():
    assert describe(ValueError("x")) == "ValueError: x"


def test_the_lock_is_exclusive(tmp_path):
    path = tmp_path / "state" / "tick.lock"
    with lock(path) as first, lock(path) as second:
        assert (first, second) == (True, False)
    with lock(path) as again:
        assert again


def test_run_turns_a_timeout_into_exit_code_124(monkeypatch):
    def hang(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", hang)
    assert _runtime.run(["launchctl", "print"]).returncode == 124


def test_tick_log_writes_json_lines_that_tail_reads(tmp_path):
    path = tmp_path / "log" / "daemon.jsonl"
    log = TickLog(path, "t1")
    log.write(AT, logging.INFO, "first", job="example:digest", error=None)
    log.write(AT, logging.ERROR, "second", receipt="abc")
    log.close()
    path.with_name("daemon.jsonl.1").write_text(
        '{"at": "2026-10-05T08:00:00+00:00", "level": "INFO", "message": "old"}\n'
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
    lines = tail(path, 3)
    assert [(line.message, line.level, line.tick, line.job, line.receipt) for line in lines] == [
        ("old", "INFO", None, None, None),
        ("first", "INFO", "t1", "example:digest", None),
        ("second", "ERROR", "t1", None, "abc"),
    ]
    assert [line.message for line in tail(path, 1)] == ["second"]  # the unreadable last line is left out


def test_tail_of_a_missing_log_is_empty(tmp_path):
    assert tail(tmp_path / "daemon.jsonl", 5) == []


def test_closing_an_unused_log_is_fine(tmp_path):
    TickLog(tmp_path / "daemon.jsonl", "t").close()
    assert not (tmp_path / "daemon.jsonl").exists()
