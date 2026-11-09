"""Owned child output remains checked after graceful teardown needs a forced kill."""

import ast
import os
import signal
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


@pytest.mark.parametrize(
    "identifier",
    [
        "heinzel/modules/system/plugin/docs/__init__.py",
        "heinzel/modules/system/plugin/docs/_build.py",
        "heinzel/modules/heinzel/_session_models.py",
        "heinzel/modules/graph/_models.py",
        "heinzel/kernel/_contributions.py",
        "pydantic/_internal/_model_construction.py",
        "rich/markdown.py",
        "markdown_it/parser_inline.py",
        "runpy.py",
    ],
)
def test_startup_snapshot_identifies_public_import_work_without_returning_path(identifier):
    report = (
        "Timeout (0:00:04)!\nThread 0x1234:\n"
        f'  File "/private/secret-owner/site-packages/{identifier}", line 63 in secret_function\n'
    )
    frames = RunningService.startup_frames(report)
    assert frames == [[(identifier, 63)]]
    assert "secret" not in repr(frames)


@pytest.mark.parametrize(
    "filename",
    [
        "private-plugin/_service.py",
        "heinzel/modules/private-customer/_service.py",
        "heinzel/modules/graph/private-token.py",
        "pydantic/private-customer.py",
        "private-token-heinzel/modules/graph/_models.py",
    ],
)
def test_startup_snapshot_keeps_unknown_plugin_and_similar_source_names_private(filename):
    report = f'Timeout (0:00:04)!\n  File "/private/secret-owner/{filename}", line 19 in secret_function\n'
    assert RunningService.startup_frames(report) == [[("other", 19)]]


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
    ("platform", "version", "isolated", "fatal_options"),
    [
        ("linux", (3, 14), True, {"all_threads": False, "c_stack": False}),
        ("linux", (3, 13), True, {"all_threads": False}),
        ("darwin", (3, 14), True, None),
        ("linux", (3, 14), False, None),
    ],
)
def test_startup_diagnostics_reuses_the_private_file_without_changing_the_watchdog(
    tmp_path, platform, version, isolated, fatal_options
):
    packages = tmp_path / "sdk/lib/python3.14/site-packages"
    packages.mkdir(parents=True)
    _ui_service.startup_diagnostics(tmp_path / "sdk", tmp_path)
    target = packages / "sitecustomize.py"
    destination = tmp_path / "startup-stacks.txt"
    assert destination.stat().st_mode & 0o777 == 0o600
    calls = []
    modules = {
        "faulthandler": SimpleNamespace(
            enable=lambda **kwargs: calls.append(("fatal", kwargs)),
            dump_traceback_later=lambda seconds, **kwargs: calls.append(("timer", seconds, kwargs)),
        ),
        "sys": SimpleNamespace(platform=platform, version_info=version, flags=SimpleNamespace(isolated=isolated)),
        "os": SimpleNamespace(
            O_WRONLY=os.O_WRONLY,
            O_TRUNC=os.O_TRUNC,
            O_NOFOLLOW=os.O_NOFOLLOW,
            open=lambda path, flags: calls.append(("open", path, flags)) or 97,
        ),
    }
    scope = {"__builtins__": {"__import__": lambda name, *args, **kwargs: modules[name]}}
    exec(compile(target.read_text(), "owned-sitecustomize", "exec"), scope)  # noqa: S102 - generated fixture code
    expected = []
    if isolated:
        expected.append(("open", str(destination), os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW))
        if fatal_options is not None:
            expected.append(("fatal", {"file": 97, **fatal_options}))
        expected.append(("timer", 4, {"repeat": False, "file": 97}))
    assert calls == expected


_FATAL_REPORT = (
    "Fatal Python error: Segmentation fault\n\nStack (most recent call first):\n"
    '  File "/private/example/pydantic/main.py", line 51 in secret_function\n'
    '  File "/private/example/private-plugin.py", line 13 in secret_function\n'
    "\nExtension modules: pydantic_core._pydantic_core, yaml._yaml, private_extension (total: 3)\n"
    "private-error-body #token=secret\n"
)


@pytest.mark.parametrize("previous", [None, "old-instance"])
def test_linux_sigsegv_stays_a_failure_and_reports_only_bound_evidence(monkeypatch, tmp_path, previous):
    monkeypatch.setattr(_ui_service.sys, "platform", "linux")
    child = SimpleNamespace(
        poll=lambda: -signal.SIGSEGV,
        communicate=lambda timeout: (b"private-error-body", b"#token=secret"),
    )
    fixture = running(child)
    fixture.service.directory = tmp_path
    fixture.service.prefix = tmp_path / "sdk"
    fixture.service.status = lambda: SimpleNamespace(ready=False)
    startup = tmp_path / "startup-stacks.txt"
    startup.write_text("discarded prefix\n" * 5000 + _FATAL_REPORT)
    startup.chmod(0o600)
    calls = []

    def installed(prefix):
        assert prefix == fixture.service.prefix
        calls.append("installed")
        return {"result": "static-installed-metadata", "configured_python": "3.14.10"}

    monkeypatch.setattr(_ui_service, "installed_versions", installed)
    with pytest.raises(pytest.fail.Exception) as failure:
        fixture.ready(previous)
    message = str(failure.value)
    assert "owned process exit=-11, signal=11, publication failure=None" in message
    evidence = ast.literal_eval(message.split("Linux SIGSEGV evidence=", 1)[1].removesuffix("."))
    expected = (
        {
            "result": "reported",
            "frames": [("pydantic/main.py", 51), ("other", 13)],
            "extensions": ["pydantic-core", "pyyaml"],
        }
        if previous is None
        else {"result": "not-initial-startup"}
    )
    assert evidence == {
        "fatal": expected,
        "installed": {"result": "static-installed-metadata", "configured_python": "3.14.10"},
    }
    assert calls == ["installed"]
    assert not any(secret in message for secret in ("/private", "secret_function", "private_", "#token="))


def test_fatal_parser_ignores_timeout_stacks_and_caps_fatal_frames():
    frame = '  File "/private/example/heinzel/kernel/_boot.py", line 15 in secret_function\n'
    old_report = "Timeout (0:00:04)!\nThread 0x1234:\n" + frame
    assert RunningService.fatal_frames(old_report) == {"result": "unavailable"}
    report = old_report + "Fatal Python error: Segmentation fault\n\nStack (most recent call first):\n" + frame * 64
    assert RunningService.fatal_frames(report)["frames"] == [("heinzel/kernel/_boot.py", 15)] * 32


def test_fatal_report_reader_refuses_a_linked_report(tmp_path):
    fixture = running(None)
    fixture.service.directory = tmp_path
    foreign = tmp_path / "outside"
    foreign.write_text(_FATAL_REPORT)
    foreign.chmod(0o600)
    (tmp_path / "startup-stacks.txt").symlink_to(foreign)
    assert fixture.fatal_metadata() == {"result": "unavailable"}


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
        clock.value = 22.125
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
        "process_wall_seconds": 12.125,
        "ready_wait_seconds": 12.125,
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
@pytest.mark.parametrize("elapsed", [0.0, 11.999])
def test_successful_readiness_keeps_existing_gate_without_any_cpu_or_native_query(
    first_ready, elapsed, monkeypatch, tmp_path
):
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

    def wait(seconds):
        waits.append(seconds)
        clock.value = 10.0 + elapsed

    monkeypatch.setattr(_ui_service.threading, "Event", lambda: SimpleNamespace(wait=wait))
    assert fixture.ready(previous="old") is current
    assert reads == [1, 1]
    assert waits == [0.02]
    assert clock.value == 10.0 + elapsed


def test_readiness_still_fails_at_the_twelve_second_deadline(monkeypatch, tmp_path):
    fixture, _child, clock = owned_fixture(monkeypatch, tmp_path)
    reads, waits = [], []

    def status():
        reads.append(clock.value)
        return SimpleNamespace(ready=False, model_dump_json=lambda: '{"ready":false}')

    def wait(seconds):
        waits.append(seconds)
        clock.value += 1.0

    fixture.service.status = status
    monkeypatch.setattr(_ui_service.threading, "Event", lambda: SimpleNamespace(wait=wait))
    monkeypatch.setattr(fixture, "wait_channel", lambda: "unavailable")
    monkeypatch.setattr(fixture, "publication_evidence", lambda **kwargs: {})
    with pytest.raises(pytest.fail.Exception, match="did not become ready with a new lifecycle instance"):
        fixture.ready()
    assert reads == [float(second) for second in range(10, 22)]
    assert waits == [0.02] * 12
    assert clock.value - fixture.ready_started == 12.0
