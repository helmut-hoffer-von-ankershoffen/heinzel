"""The desktop step helper rides on the kernel logging standard (ADR 0138): one start and one outcome event."""

import pytest

from heinzel.heads import _desktop_log
from heinzel.heads._desktop_messages import Problem

pytestmark = pytest.mark.unit


class FailureError(Exception):
    def __init__(self) -> None:
        super().__init__("no room")
        self.problem = Problem("There is no room.", "Free some space.")


def _events(caplog):
    return [r for r in caplog.records if r.name == "heinzel.desktop"]


def test_a_step_logs_its_start_and_an_outcome_with_the_state(caplog):
    caplog.set_level("INFO", logger="heinzel.desktop")
    with _desktop_log.step("install", "release", path="/apps/Hz.app") as result:
        result["outcome"] = "installed"
    start, end = _events(caplog)
    assert (start.getMessage(), start.phase, start.operation, start.step, start.path) == (
        "desktop.install.release",
        "start",
        "install",
        "release",
        "/apps/Hz.app",
    )
    assert (end.phase, end.outcome, end.state, end.levelname) == ("end", "ok", "installed", "INFO")
    assert isinstance(end.duration_ms, int)
    assert start.correlation_id == end.correlation_id


def test_a_failing_step_logs_the_two_lines_and_the_error_type(caplog):
    caplog.set_level("INFO", logger="heinzel.desktop")
    with pytest.raises(FailureError), _desktop_log.step("install", "run"):
        raise FailureError
    end = _events(caplog)[-1]
    assert (end.outcome, end.error_type, end.error, end.levelname) == ("error", "FailureError", "no room", "ERROR")
    assert (end.happened, end.action) == ("There is no room.", "Free some space.")
