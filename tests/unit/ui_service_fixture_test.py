"""Owned child output remains checked after graceful teardown needs a forced kill."""

import subprocess
from types import SimpleNamespace

import pytest

from tests.e2e._ui_service import RunningService

pytestmark = pytest.mark.unit


class HungChild:
    def __init__(self, output=b"", errors=b""):
        self.output, self.errors = output, errors
        self.killed = False
        self.terminated = False

    def poll(self):
        return -9 if self.killed else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def communicate(self, timeout):
        if not self.killed:
            command = "owned-child"
            raise subprocess.TimeoutExpired(command, timeout)
        return self.output, self.errors


def running(child):
    service = SimpleNamespace(_control=lambda *args: None)
    result = RunningService(service, None, [], {})
    result.processes.append(child)
    return result


def test_forced_kill_does_not_skip_the_existing_session_url_guard():
    child = HungChild(output=b"/#token=" + b"t" * 43)
    other = HungChild()
    fixture = running(child)
    fixture.processes.append(other)
    with pytest.raises(AssertionError, match="session URL"):
        fixture.close()
    assert child.killed
    assert other.killed
    assert child.terminated
    assert other.terminated


def test_forced_kill_reports_only_safe_traceback_metadata():
    child = HungChild(
        errors=(
            b'Traceback (most recent call last):\n  File "/private/example/_supervision.py", line 42, in publish\n'
            b"RuntimeError: private-error-body-must-not-be-logged\n"
        )
    )
    fixture = running(child)
    with pytest.raises(AssertionError, match="unexpected traceback") as failure:
        fixture.close()
    message = str(failure.value)
    assert "RuntimeError" in message
    assert "_supervision.py" in message
    assert "private-error-body-must-not-be-logged" not in message
    assert "/private/example" not in message
    assert child.killed


def test_native_sample_exposes_only_bounded_static_thread_and_symbol_labels():
    report = (
        "Path: /private/private-error-body-must-not-be-logged\n"
        " 46 Thread_123: Thread-1 (when_started)\n"
        " + 46 fsync (in libsystem_kernel.dylib)\n"
        " + 46 private_error_body_must_not_be_logged (in private-library)\n"
        "Binary Images:\n /private/private-error-body-must-not-be-logged\n"
    )
    assert RunningService.sample_metadata(report) == [{"role": "publication", "symbols": ["fsync"]}]
    many_threads = "\n".join(f" 46 Thread_{number}: private-thread-name" for number in range(32))
    assert len(RunningService.sample_metadata(many_threads)) == 16
