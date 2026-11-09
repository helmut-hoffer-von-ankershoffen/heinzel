"""Exercise the isolated CLI reviewer with a private socket and a real PTY."""

import hashlib
import json
import os
import pty
import re
import select
import socket
import subprocess
import sys
import termios
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from threading import Event

import pytest

from heinzel import HeinzelError
from heinzel.heads import cli
from heinzel.heads.mcp._ipc_admission import McpGeneration
from heinzel.heads.mcp._ipc_approval import PrivateMcpPlan
from heinzel.heads.mcp._ipc_cli_review import _REVIEWER, _REVIEWER_SHA256
from heinzel.heads.mcp._ipc_supervisor import owned_mcp_supervisor
from heinzel.heads.mcp._ipc_work import McpWorkRequest
from heinzel.kernel._approval import arguments_hash

pytestmark = pytest.mark.unit

_LAUNCHER = """
import fcntl, os, socket, sys, termios
from pathlib import Path
from heinzel import HeinzelError
from heinzel.heads.mcp import _ipc_cli_review as cli
if sys.argv[1] == 'tty':
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
if sys.argv[1] in {'changed', 'replace'}:
    cli._REVIEWER = Path.cwd() / 'reviewer.py'
if sys.argv[1] == 'replace':
    verify = cli._verified_source
    def replace_after_verification(path, digest):
        source = verify(path, digest)
        (Path.cwd() / 'substitute.py').replace(path)
        return source
    cli._verified_source = replace_after_verification
with socket.socket(fileno=int(sys.argv[2])) as channel:
    try:
        accepted = cli.run_cli_host_reviewer(channel)
    except HeinzelError:
        raise SystemExit(2) from None
    assert channel.fileno() == -1
raise SystemExit(0 if accepted else 1)
"""


def test_installed_pin_matches_the_bundled_reviewer():
    assert hashlib.sha256(_REVIEWER.read_bytes()).hexdigest() == _REVIEWER_SHA256


@pytest.fixture
def offer():
    review = {
        "instance": "pending-fixture",
        "challenge": "broker-challenge-hidden",
        "registration": "fixture-registration",
        "generation": 3,
        "terms": {"targets": ["personal\x1b[2J\u202e"], "result_read": True, "usd_limit": 0},
    }
    digest = arguments_hash(review)
    return json.dumps({"review": review, "digest": digest}, ensure_ascii=True).encode() + b"\n", digest


def launch_fixture(channel, cwd, *, terminal=None, mode=""):
    fixture_mode = mode or ("tty" if terminal is not None else "none")
    return subprocess.Popen(
        [
            sys.executable,
            "-E" if fixture_mode == "unisolated" else "-I",
            "-c",
            _LAUNCHER,
            fixture_mode,
            str(channel.fileno()),
        ],
        cwd=cwd,
        env=os.environ | {"PYTHONPATH": os.fspath(cwd), "PYTHONSTARTUP": os.fspath(cwd / "sitecustomize.py")},
        pass_fds=(channel.fileno(),),
        stdin=terminal if terminal is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def read_prompt(terminal):
    deadline = time.monotonic() + 2
    output = bytearray()
    while b"\r\n> " not in output:
        remaining = deadline - time.monotonic()
        assert remaining > 0
        assert select.select([terminal], [], [], remaining)[0]
        output.extend(os.read(terminal, 4096))
    return bytes(output)


@pytest.mark.parametrize("approve", [False, True], ids=["decline", "exact-confirmation"])
def test_inherited_reviewer_requires_exact_foreground_terminal_answer(tmp_path, offer, approve):
    marker = tmp_path / "injected"
    injection = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    (tmp_path / "sitecustomize.py").write_text(injection)
    (tmp_path / "json.py").write_text(injection)
    master, terminal = pty.openpty()
    settings = termios.tcgetattr(terminal)
    settings[3] &= ~termios.ECHO
    termios.tcsetattr(terminal, termios.TCSANOW, settings)
    broker, channel = socket.socketpair()
    payload, digest = offer
    try:
        with broker, channel:
            broker.settimeout(2)
            broker.sendall(payload)
            with launch_fixture(channel, tmp_path, terminal=terminal) as process:
                os.close(terminal)
                terminal = -1
                channel.close()
                try:
                    prompt = read_prompt(master)
                    assert b"pending-fixture" in prompt
                    assert b"personal\\u001b[2J\\u202e" in prompt
                    assert b"broker-challenge-hidden" not in prompt
                    assert digest.encode() not in prompt
                    match = re.search(rb"Type exactly: (approve MCP [0-9a-f]{8})\r\n", prompt)
                    assert match is not None
                    os.write(master, (match[1] if approve else b"yes") + b"\n")
                    with broker.makefile("rb") as response:
                        assert response.readline() == (f"approve {digest}\n".encode() if approve else b"")
                        assert response.read(1) == b""
                    assert process.communicate(timeout=2) == (b"", b"")
                    assert process.returncode == (0 if approve else 1)
                    assert not marker.exists()
                finally:
                    if process.poll() is None:
                        os.close(master)  # Unblock Darwin's controlling-terminal drain before reaping the fixture.
                        master = -1
                        process.kill()
    finally:
        if master >= 0:
            os.close(master)
        if terminal >= 0:
            os.close(terminal)


def test_same_uid_without_a_controlling_terminal_cannot_send_a_reply(tmp_path, offer):
    broker, channel = socket.socketpair()
    with broker, channel:
        broker.sendall(offer[0])
        with launch_fixture(channel, tmp_path) as process:
            channel.close()
            try:
                assert process.communicate(timeout=2) == (b"", b"")
                assert process.returncode == 1
                # An unread offer may produce a reset when the other end closes. Neither form contains a reply.
                broker.settimeout(2)
                with suppress(ConnectionResetError):
                    assert broker.recv(1) == b""
            finally:
                if process.poll() is None:
                    process.kill()


def test_changed_reviewer_bytes_refuse_before_use(tmp_path):
    (tmp_path / "reviewer.py").write_text("def review(channel): return True\n")
    broker, channel = socket.socketpair()
    with broker, channel, launch_fixture(channel, tmp_path, mode="changed") as process:
        channel.close()
        assert process.communicate(timeout=2) == (b"", b"mcp.ipc.load_reviewer\n")
        assert process.returncode == 2
        broker.settimeout(2)
        assert broker.recv(1) == b""


def test_unisolated_runtime_refuses_before_review(tmp_path):
    broker, channel = socket.socketpair()
    with broker, channel, launch_fixture(channel, tmp_path, mode="unisolated") as process:
        channel.close()
        assert process.communicate(timeout=2) == (b"", b"mcp.ipc.load_reviewer\n")
        assert process.returncode == 2
        broker.settimeout(2)
        assert broker.recv(1) == b""


def test_reviewer_replacement_after_digest_check_cannot_reply_without_terminal(tmp_path, offer):
    candidate = tmp_path / "reviewer.py"
    candidate.write_bytes(_REVIEWER.read_bytes())
    marker = tmp_path / "replacement-executed"
    payload, digest = offer
    replacement = (
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
        f"def review(channel):\n    channel.sendall({f'approve {digest}\n'.encode()!r})\n    return True\n"
    )
    (tmp_path / "substitute.py").write_text(replacement)
    broker, channel = socket.socketpair()
    with broker, channel:
        broker.sendall(payload)
        with launch_fixture(channel, tmp_path, mode="replace") as process:
            channel.close()
            assert process.communicate(timeout=2) == (b"", b"")
            assert process.returncode == 1
            broker.settimeout(2)
            with suppress(ConnectionResetError):
                assert broker.recv(1) == b""
    assert candidate.read_text() == replacement
    assert not marker.exists()


_SUPERVISOR_LAUNCHER = """
import fcntl, runpy, sys, termios
from pathlib import Path
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
runpy.run_path(sys.argv[1])['_run_supervised_fixture'](Path(sys.argv[2]), sys.argv[3])
"""


def _pending(supervisor, count):
    deadline = time.monotonic() + 2
    while len(ids := supervisor.pending_review_ids()) != count:
        assert time.monotonic() < deadline
        time.sleep(0.005)
    return ids


def _fake_dispatch(transport, generation, admitted, release, calls):
    request = McpWorkRequest(target="fixture", operation="read-fixture", request_id="one", input_hash="a" * 64)
    try:
        with transport.admit(lambda _: generation) as lease:
            admitted.set()
            assert release.wait(2)
            assert transport.record(lease, request, "reserve") == "queued"
            assert transport.record(lease, request, "start") == "running"
            lease.require_active()
            calls.append("fake-provider")
            assert transport.record(lease, request, "complete") == "completed"
            assert transport.record(lease, request, "reserve") == "completed"
            with pytest.raises(HeinzelError, match="work authority is invalid or unavailable"):
                transport.record(lease, request, "start")
            return "completed"
    except HeinzelError:
        return "refused"


def _review_command(supervisor, instance_id):
    def forbidden_boot():
        pytest.fail("The host review command booted the project kernel")

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(sys, "argv", ["hz", "system", "mcp", "review", instance_id])
        patcher.setattr(cli, "boot", forbidden_boot)
        with pytest.raises(SystemExit) as exit_status:
            cli.main(host_mcp_supervisor=supervisor)
    return exit_status.value.code


def _run_supervised_fixture(directory, phase):
    generation = McpGeneration("fixture-registration", 3)
    now = 100.0
    plan = PrivateMcpPlan.capture(generation, "fixture-grant", 1, 2000.0, {"dispatch": {"fixture": ["read-fixture"]}})
    endpoint = directory / "sock"
    release = Event()
    first_admitted, second_admitted = Event(), Event()
    calls = []
    with (
        socket.socket(socket.AF_UNIX) as listener,
        socket.socket(socket.AF_UNIX) as first,
        socket.socket(socket.AF_UNIX) as second,
    ):
        listener.bind(os.fspath(endpoint))
        endpoint.chmod(0o600)
        listener.listen()
        listener.settimeout(2)
        with (
            owned_mcp_supervisor(listener, endpoint, directory / "state", plan, clock=lambda: now) as supervisor,
            ThreadPoolExecutor(max_workers=2) as workers,
        ):
            transport = supervisor.transport
            assert not hasattr(transport, "review")
            first.connect(os.fspath(endpoint))
            first_result = workers.submit(_fake_dispatch, transport, generation, first_admitted, release, calls)
            try:
                (intended,) = _pending(supervisor, 1)
                second.connect(os.fspath(endpoint))
                second_result = workers.submit(_fake_dispatch, transport, generation, second_admitted, release, calls)
                assert intended in _pending(supervisor, 2)
                status = _review_command(supervisor, intended)
                if phase == "decline":
                    assert status == 1
                    supervisor.revoke()
                else:
                    assert status == 0
                    assert first_admitted.wait(2)
                    assert not second_admitted.is_set()
                    if phase == "expire":
                        now = 1001.0
                    elif phase == "revoke":
                        supervisor.revoke()
                release.set()
                outcome = first_result.result(timeout=2)
                supervisor.revoke()
                assert second_result.result(timeout=2) == "refused"
            finally:
                release.set()
                supervisor.revoke()
        with pytest.raises(HeinzelError, match="no longer owns the registration"):
            supervisor.revoke()
    sys.stdout.write(json.dumps({"first": outcome, "second_admitted": second_admitted.is_set(), "calls": calls}))


@pytest.mark.parametrize("phase", ["approve", "decline", "expire", "revoke"])
def test_public_review_command_reviews_one_connection_and_fences_fake_dispatch(tmp_path_factory, phase):
    directory = tmp_path_factory.mktemp("svp").resolve()
    master, terminal = pty.openpty()
    settings = termios.tcgetattr(terminal)
    settings[3] &= ~termios.ECHO
    termios.tcsetattr(terminal, termios.TCSANOW, settings)
    try:
        with subprocess.Popen(
            [sys.executable, "-I", "-c", _SUPERVISOR_LAUNCHER, __file__, os.fspath(directory), phase],
            stdin=terminal,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        ) as process:
            os.close(terminal)
            terminal = -1
            try:
                prompt = read_prompt(master)
                assert b'"registration": "fixture-registration"' in prompt
                assert b'"grant": "fixture-grant"' in prompt
                match = re.search(rb"Type exactly: (approve MCP [0-9a-f]{8})\r\n", prompt)
                assert match is not None
                os.write(master, (b"no" if phase == "decline" else match[1]) + b"\n")
                stdout, stderr = process.communicate(timeout=2)
                assert process.returncode == 0, stderr.decode()
                messages = stdout.splitlines()
                assert messages[:-1] == (
                    [] if phase == "decline" else [b"The broker accepted the selected MCP connection review."]
                )
                assert json.loads(messages[-1]) == {
                    "first": "completed" if phase == "approve" else "refused",
                    "second_admitted": False,
                    "calls": ["fake-provider"] if phase == "approve" else [],
                }
            finally:
                if process.poll() is None:
                    os.close(master)
                    master = -1
                    process.kill()
    finally:
        if master >= 0:
            os.close(master)
        if terminal >= 0:
            os.close(terminal)


@pytest.mark.parametrize(
    ("arguments", "status", "message"),
    [
        (["--help"], 0, b"Usage: hz system mcp review PENDING_ID\n"),
        (["x" * 32, "--yes"], 2, b"Usage: hz system mcp review PENDING_ID\n"),
        (["x" * 32], 1, b"Local MCP review requires the active owned supervisor.\n"),
    ],
)
def test_standalone_review_command_parses_offline_and_ignores_forged_descriptor(tmp_path, arguments, status, message):
    # An ordinary same-user process can inherit a descriptor and set these variables, but neither grants authority.
    broker, impostor = socket.socketpair()
    with broker, impostor:
        result = subprocess.run(
            [sys.executable, "-I", "-m", "heinzel", "system", "mcp", "review", *arguments],
            cwd=tmp_path,
            env={"HZ_CONFIG_HOME": "/unavailable", "HZ_MCP_REVIEW_FD": str(impostor.fileno())},
            pass_fds=(impostor.fileno(),),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=2,
            check=False,
        )
        assert result.returncode == status
        if status == 0:
            assert result.stdout.startswith(message)
            assert not result.stderr
        else:
            assert result.stderr == message
            assert not result.stdout
        impostor.close()
        broker.settimeout(2)
        assert broker.recv(1) == b""
