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


def test_python_startup_snapshots_expose_only_bounded_fixed_source_labels():
    snapshot = (
        "Timeout (0:00:02)!\nThread 0x1234:\n"
        '  File "/private/example/heinzel/heads/cli/__init__.py", line 42 in private_function_name\n'
        '  File "/private/example/private-file.py", line 7 in private_function_name\n'
        "private-error-body-must-not-be-logged\n"
    )
    frames = RunningService.startup_frames(snapshot * 5)
    assert frames == [[("heinzel/heads/cli/__init__.py", 42), ("other", 7)]] * 3
    many_frames = "Timeout (0:00:02)!\n" + (snapshot.splitlines()[2] + "\n") * 64
    assert len(RunningService.startup_frames(many_frames)[0]) == 32


@pytest.mark.parametrize("returncode", [0, 75, -9])
def test_early_exit_reports_returncode_and_signal_without_child_output(returncode):
    secret = b"private-error-body-must-not-be-logged /private/example #token=secret"
    child = SimpleNamespace(
        poll=lambda: returncode,
        communicate=lambda timeout: (secret, b"Owned web publication failed: category=os errno=13\n" + secret),
    )
    fixture = running(child)
    fixture.service.status = lambda: SimpleNamespace(ready=False)
    with pytest.raises(pytest.fail.Exception) as failure:
        fixture.ready()
    message = str(failure.value)
    assert f"owned process exit={returncode}" in message
    assert f"signal={-returncode if returncode < 0 else None}" in message
    assert "{'category': 'os', 'errno': 13}" in message
    assert "private-error-body" not in message
    assert "/private/example" not in message
    assert "#token=" not in message


@pytest.mark.parametrize(
    "encoded", [b"category=secret errno=13", b"category=os errno=13 /private/example", b"category=os errno=99999"]
)
def test_publication_diagnostics_refuse_unknown_or_unbounded_labels(encoded):
    assert RunningService.publication_failure(b"Owned web publication failed: " + encoded) is None
