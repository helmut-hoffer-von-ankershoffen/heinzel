"""Owned child output remains checked after graceful teardown needs a forced kill."""

import ast
import subprocess
from types import SimpleNamespace

import psutil
import pytest

from tests.e2e import _ui_service
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


def owned_fixture(monkeypatch, tmp_path):
    clock = SimpleNamespace(value=10.0)
    child = SimpleNamespace(pid=4242, poll=lambda: None)
    service = SimpleNamespace(_control=lambda *args: None, directory=tmp_path, runtime=tmp_path / "runtime.json")
    fixture = RunningService(service, SimpleNamespace(), [], {})
    monkeypatch.setattr(_ui_service.time, "monotonic", lambda: clock.value)
    monkeypatch.setattr(_ui_service.subprocess, "Popen", lambda *args, **kwargs: child)
    fixture.launch()
    return fixture, child, clock


@pytest.mark.parametrize("observation", ["valid", "unavailable", "query-unavailable", "exited", "exit-during-query"])
def test_readiness_failure_observes_only_owned_cpu_before_native_sampling(observation, monkeypatch, tmp_path):
    fixture, child, clock = owned_fixture(monkeypatch, tmp_path)
    events, waits = [], []
    calls = 0
    exited = False

    def poll():
        nonlocal calls
        calls += 1
        return 0 if exited or (observation == "exited" and calls >= 3) else None

    def status():
        clock.value = 16.125
        return SimpleNamespace(ready=False, model_dump_json=lambda: '{"ready":false}')

    def process(pid):
        assert pid == child.pid == 4242
        events.append("cpu")
        if observation == "unavailable":
            raise psutil.AccessDenied(pid, msg="private-error-body /private/example #token=secret")

        def cpu_times():
            nonlocal exited
            if observation == "query-unavailable":
                message = "private-error-body /private/example #token=secret"
                raise OSError(message)
            exited = observation == "exit-during-query"
            return SimpleNamespace(user=1.25, system=0.5, args="private-error-body", environment="secret")

        return SimpleNamespace(cpu_times=cpu_times)

    def native():
        events.append("native")
        clock.value = 99.0
        return {"result": "sampled", "threads": []}

    child.poll = poll
    fixture.service.status = status
    monkeypatch.setattr(psutil, "Process", process)
    monkeypatch.setattr(_ui_service.threading, "Event", lambda: SimpleNamespace(wait=waits.append))
    monkeypatch.setattr(fixture, "wait_channel", lambda: "unavailable")
    monkeypatch.setattr(fixture, "native_state", native)
    with pytest.raises(pytest.fail.Exception) as failure:
        fixture.ready()
    message = str(failure.value)
    assert "did not become ready with a new lifecycle instance" in message
    assert "failed status calls=0" in message
    assert waits == [0.02]
    evidence = ast.literal_eval(message.split("publication evidence=", 1)[1].removesuffix("."))
    expected = {
        "result": {
            "valid": "sampled",
            "unavailable": "unavailable",
            "query-unavailable": "unavailable",
            "exited": "exited",
            "exit-during-query": "exited",
        }[observation],
        "process_wall_seconds": 6.125,
        "ready_wait_seconds": 6.125,
    }
    if observation == "valid":
        expected.update(process_user_seconds=1.25, process_system_seconds=0.5)
    assert evidence["cpu"] == expected
    assert events == (["native"] if observation == "exited" else ["cpu", "native"])
    assert not any(secret in message for secret in ("private-error-body", "/private/example", "#token=", "environment"))


@pytest.mark.parametrize("pid", [None, True, 0, -1, "4242"])
def test_cpu_observation_refuses_unknown_child_identity(pid, monkeypatch, tmp_path):
    fixture, child, clock = owned_fixture(monkeypatch, tmp_path)
    child.pid = pid
    fixture.ready_started = 10.0
    clock.value = 16.0
    monkeypatch.setattr(psutil, "Process", lambda pid: pytest.fail("An unknown PID must never be queried"))
    assert fixture.startup_cpu() == {"result": "unavailable"}


def test_cpu_observation_refuses_a_process_without_the_recorded_owned_launch(monkeypatch, tmp_path):
    fixture, child, _clock = owned_fixture(monkeypatch, tmp_path)
    fixture.processes.append(SimpleNamespace(pid=child.pid, poll=lambda: None))
    monkeypatch.setattr(psutil, "Process", lambda pid: pytest.fail("Only the recorded owned process may be queried"))
    assert fixture.startup_cpu() == {"result": "unavailable"}


@pytest.mark.parametrize("elapsed", [-1, 601, float("nan"), float("inf")])
def test_cpu_observation_refuses_unbounded_elapsed_time(elapsed, monkeypatch, tmp_path):
    fixture, _child, clock = owned_fixture(monkeypatch, tmp_path)
    fixture.ready_started = 10.0
    clock.value += elapsed
    monkeypatch.setattr(psutil, "Process", lambda pid: pytest.fail("Unbounded wall time must remain unavailable"))
    assert fixture.startup_cpu() == {"result": "unavailable"}


@pytest.mark.parametrize(
    "value",
    [-1, 601, float("nan"), float("inf"), True, "private-error-body", pytest.param(10**1000, id="oversized-integer")],
)
def test_cpu_observation_exposes_only_finite_bounded_seconds(value, monkeypatch, tmp_path):
    fixture, _child, clock = owned_fixture(monkeypatch, tmp_path)
    fixture.ready_started = 10.0
    clock.value = 16.0
    monkeypatch.setattr(
        psutil, "Process", lambda pid: SimpleNamespace(cpu_times=lambda: SimpleNamespace(user=value, system=0.5))
    )
    assert fixture.startup_cpu() == {"result": "unavailable", "process_wall_seconds": 6.0, "ready_wait_seconds": 6.0}


@pytest.mark.parametrize("first_ready", [False, True])
def test_successful_readiness_keeps_existing_gate_without_any_cpu_or_native_query(first_ready, monkeypatch, tmp_path):
    fixture, _child, clock = owned_fixture(monkeypatch, tmp_path)
    old = SimpleNamespace(ready=first_ready, instance="old")
    current = SimpleNamespace(ready=True, instance="new")
    responses = iter([old, current])
    reads, waits = [], []

    def status():
        reads.append(1)
        return next(responses)

    fixture.service.status = status
    monkeypatch.setattr(psutil, "Process", lambda pid: pytest.fail("Successful startup must not query CPU"))
    monkeypatch.setattr(fixture, "native_state", lambda: pytest.fail("Successful startup must not sample threads"))
    monkeypatch.setattr(_ui_service.threading, "Event", lambda: SimpleNamespace(wait=waits.append))
    assert fixture.ready(previous="old") is current
    assert reads == [1, 1]
    assert waits == [0.02]
    assert clock.value == 10.0
