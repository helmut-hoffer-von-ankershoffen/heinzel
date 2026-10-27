"""The native broker owns real descriptors and only its private worker; no GUI or platform permission is used."""

import fcntl
import hashlib
import json
import os
import selectors
import signal
import socket
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

pytestmark = pytest.mark.integration
BROKER = Path(__file__).parents[2] / "desktop" / "managed" / "runtime" / "broker.py"
HTML = (
    b"<!doctype html><html><head><title>Fixture</title></head><body>verified fixture app"
    b'<pre id="state">Loading</pre><script>window.hzDesktop.snapshot().then(value => '
    b'document.querySelector("#state").textContent = JSON.stringify(value));</script></body></html>'
)
WORKER = (Path(__file__).parents[1] / "fixtures" / "managed_backend_http.py").read_text()


@dataclass(frozen=True, slots=True)
class Installed:
    """Fixture state may advance after the controller captured generation one."""

    generation: int = 1
    prepared: bool = False


def launch(tmp_path, *, port=0, worker=WORKER, profile=None, installed=None):
    installed = installed or Installed()
    actual_generation = installed.generation
    directory = profile or tmp_path / "profile"
    directory.mkdir(mode=0o700, exist_ok=True)
    python = Path(sys.executable).resolve()
    manifest = {
        "schema": 1,
        "core_version": "0.7.12",
        "python": str(python),
        "python_sha256": hashlib.sha256(python.read_bytes()).hexdigest(),
        "ui_sha256": hashlib.sha256(HTML).hexdigest(),
        "files": {"worker.py": hashlib.sha256(worker.encode()).hexdigest()},
    }
    encoded = (json.dumps(manifest) + "\n").encode()
    deployment = hashlib.sha256(encoded).hexdigest()
    lifecycle = directory / "lifecycle"
    lifecycle.mkdir(mode=0o700, exist_ok=True)
    deployments = lifecycle / "deployments"
    deployments.mkdir(mode=0o700, exist_ok=True)
    runtime = deployments / deployment
    runtime.mkdir(mode=0o700, exist_ok=True)
    source = runtime / "worker.py"
    for path, content in (
        (source, worker.encode()),
        (runtime / "runtime.json", encoded),
        (
            lifecycle / "active.json",
            (
                json.dumps({"schema": 1, "generation": actual_generation, "deployment": deployment, "previous": None})
                + "\n"
            ).encode(),
        ),
    ):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
    if installed.prepared:
        pending = {
            "schema": 1,
            "id": "12345678-1234-4234-8234-123456789abc",
            "before": {"schema": 1, "generation": actual_generation, "deployment": deployment, "previous": None},
            "candidate": deployment,
            "phase": "prepared",
            "action": "update",
            "generation": actual_generation + 1,
        }
        descriptor = os.open(lifecycle / "transition.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(pending, output)
    process = subprocess.Popen(
        [str(python), "-I", "-S", "-B", str(BROKER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)},
    )
    process.stdin.write(
        (
            json.dumps(
                {
                    "nonce": "test-controller-nonce",
                    "port": port,
                    "worker": str(source),
                    "worker_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "version": "0.7.12",
                    "ui_sha256": hashlib.sha256(HTML).hexdigest(),
                    "profile": str(directory),
                    "deployment": deployment,
                    "generation": 1,
                }
            )
            + "\n"
        ).encode()
    )
    process.stdin.flush()
    return process


def event(process):
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(8):
            pytest.fail("The owned broker did not produce a bounded protocol response")
        line = process.stdout.readline(20_000)
        assert line, "The owned broker stopped without a protocol response"
        return json.loads(line)


def close(process):
    try:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)
        pytest.fail("The owned broker did not stop after its controller lease closed")
    finally:
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()


def identity(ready):
    url = urlsplit(ready["url"])
    assert url.scheme == "http"
    assert url.hostname == "127.0.0.1"
    token = parse_qs(url.fragment)["token"][0]
    request = urllib.request.Request(  # noqa: S310 - explicitly checked owned loopback HTTP fixture
        f"{url.scheme}://{url.netloc}/api/lifecycle", headers={"Authorization": f"Bearer {token}"}
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=2) as response:
        return json.load(response)


def test_retained_listener_recovers_a_crashed_worker_with_same_origin_and_new_verified_instance(tmp_path):
    process = launch(tmp_path)
    try:
        first = event(process)
        assert first["event"] == "ready"
        before = identity(first)
        os.kill(before["pid"], signal.SIGKILL)
        second = event(process)
        assert second["event"] == "ready"
        assert second["url"] == first["url"]
        assert second["instance"] != first["instance"]
        assert identity(second)["pid"] != before["pid"]
    finally:
        close(process)


def test_stopped_controller_can_reopen_its_stable_origin_without_waiting(tmp_path):
    first = launch(tmp_path)
    try:
        before = event(first)
        assert before["event"] == "ready"
        identity(before)
    finally:
        close(first)
    port = urlsplit(before["url"]).port
    reopened = launch(tmp_path, port=port)
    try:
        after = event(reopened)
        assert after["event"] == "ready"
        assert urlsplit(after["url"]).netloc == urlsplit(before["url"]).netloc
        assert after["instance"] != before["instance"]
        assert identity(after)["instance"] == after["instance"]
    finally:
        close(reopened)


def test_broker_rejects_activation_changed_after_the_controller_captured_it(tmp_path):
    stale = launch(tmp_path, installed=Installed(generation=2))
    try:
        refused = event(stale)
        assert refused["event"] == "failed"
        assert "url" not in refused
        assert stale.wait(timeout=2) == 1
    finally:
        close(stale)


def test_transition_authority_prevents_broker_startup_until_ownership_can_be_linearized(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    lifecycle = profile / "lifecycle"
    lifecycle.mkdir(mode=0o700)
    descriptor = os.open(lifecycle / "lifecycle.lock", os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        refused = launch(tmp_path, profile=profile)
        try:
            assert event(refused)["event"] == "failed"
        finally:
            close(refused)
    finally:
        os.close(descriptor)
    started = launch(tmp_path, profile=profile)
    try:
        assert event(started)["event"] == "ready"
    finally:
        close(started)


def test_prepared_transition_blocks_new_backend_claims_before_the_pointer_switch(tmp_path):
    process = launch(tmp_path, installed=Installed(prepared=True))
    try:
        assert event(process)["event"] == "failed"
    finally:
        close(process)


def test_profile_lock_refuses_a_second_controller(tmp_path):
    original = launch(tmp_path)
    try:
        assert event(original)["event"] == "ready"
        duplicate = launch(tmp_path)
        try:
            assert event(duplicate)["event"] == "failed"
        finally:
            close(duplicate)
        assert original.poll() is None
    finally:
        close(original)


@pytest.mark.parametrize("address", ["0.0.0.0", "127.0.0.1"])  # noqa: S104 - adversarial listener fixture
@pytest.mark.parametrize("reuse", [False, True])
def test_foreign_listeners_are_never_overlapped_or_adopted(tmp_path, address, reuse):
    with socket.socket() as occupied:
        if reuse:
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupied.bind((address, 0))
        occupied.listen()
        refused = launch(tmp_path, port=occupied.getsockname()[1])
        try:
            assert event(refused)["event"] == "failed"
            assert refused.wait(timeout=2) == 1
        finally:
            close(refused)


def test_killed_controller_broker_does_not_leave_its_worker_listening(tmp_path):
    process = launch(tmp_path)
    try:
        ready = event(process)
        before = identity(ready)
        process.kill()
        process.wait(timeout=3)
        # The inherited pipe is the worker's lease; wait on actual process termination without sleeping.
        with selectors.DefaultSelector() as selector:
            selector.register(process.stderr, selectors.EVENT_READ)
            assert selector.select(5), "Worker retained broker stderr after its lease was lost"
            assert process.stderr.read(1) == b""
        with pytest.raises(ProcessLookupError):
            os.kill(before["pid"], 0)
    finally:
        close(process)


def test_worker_crash_loop_is_bounded_and_never_becomes_ready(tmp_path):
    process = launch(tmp_path, worker="raise SystemExit(2)\n")
    try:
        failed = event(process)
        assert failed["event"] == "failed"
        assert failed["reason"] == "backend-crash-loop"
        assert "url" not in failed
    finally:
        close(process)
