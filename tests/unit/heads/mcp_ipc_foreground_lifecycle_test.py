"""Installed CLI foreground lifecycle and isolated native-manager preflight contracts."""

import hashlib
import json
import os
import pty
import re
import runpy
import select
import shutil
import socket
import subprocess
import sys
import termios
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from heinzel.heads import cli, web
from heinzel.heads.mcp import _ipc_lifecycle as lifecycle
from heinzel.heads.mcp import _ipc_runtime_root as roots
from heinzel.heads.mcp._client_config import ClientBridgeBinding, ClientConfigContext, McpClient
from heinzel.heads.mcp._client_config_files import PinnedClientBridge
from heinzel.heads.mcp._ipc_admission import McpGeneration
from heinzel.heads.mcp._ipc_approval import PrivateMcpPlan
from heinzel.heads.mcp._ipc_install_review import ManualInstallAuthority, McpInstallReview
from heinzel.heads.web._devices import Devices
from heinzel.kernel import (
    HeinzelError,
    HostServiceManager,
    Kernel,
    McpLifecycleObservation,
    McpLifecyclePlanning,
    Settings,
    host_maintenance_directory,
)
from heinzel.kernel._approval import arguments_hash
from heinzel.kernel._mcp_native import McpNativePlan
from heinzel.kernel._mcp_storage import InstalledMcpRuntime

pytestmark = pytest.mark.unit

_REGISTRATION = "fixture-foreground"
_START_REFUSED = {"decline", "changed", "manager-changed", "wrong-start-ack", "handoff-changed"}
_MANUAL_SUCCESS = {"manual-claude", "manual-codex", "manual-web"}
_LAUNCHER = """
import fcntl, runpy, sys, termios
from pathlib import Path
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
runpy.run_path(sys.argv[1])['_exercise_installed_cli'](Path(sys.argv[2]), sys.argv[3])
"""


class _Manager(HostServiceManager):
    def __init__(self, stdout, code=0):
        self.stdout = stdout
        self.code = code
        self.commands = []

    def command(self, arguments):
        self.commands.append(arguments)
        return subprocess.CompletedProcess(arguments, self.code, self.stdout, "")


def _invoke(patcher, host, action, registration=_REGISTRATION, *, handoff=None):
    argv = ["hz", "system", "mcp", action, registration]
    if handoff is not None:
        argv += ["--handoff", handoff]
    patcher.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as status:
        cli.main(host_mcp_lifecycle=host)
    return status.value.code


@contextmanager
def _planning_client(host, directory, *, validate_install=None):
    kernel = Kernel(Settings(state_dir=directory / "web-state"), [])
    kernel.bind(
        McpLifecyclePlanning, McpLifecyclePlanning(host.status, host.prepare, validate_install=validate_install)
    )
    app = web.build(kernel, port=8000, token="fixture-session", static=None, devices=Devices(directory / "devices"))
    with TestClient(
        app, base_url="http://localhost:8000", headers={"Authorization": "Bearer fixture-session"}
    ) as client:
        yield client


def _prepare_from_web(host, directory, action, *, validate_install=None):
    with _planning_client(host, directory, validate_install=validate_install) as client:
        endpoint = f"/api/mcp/lifecycle/{_REGISTRATION}"
        status = client.get(endpoint).json()
        assert status["mcp_readiness"] == status["client"] == "unverified"
        assert action in status["actions"]
        response = client.post(endpoint + "/prepare", params={"action": action})
        assert response.status_code == 200, response.text
        plan = response.json()
        assert plan["session"] == ("terminal" if action in {"start", "install"} else "owning-session")
        assert client.get(f"/api/mcp/handoffs/{plan['id']}").json() == plan
        assert str(directory) not in response.text
        assert "fixture-only" not in response.text
        return plan["handoff"]


def _exercise_running(patcher, host, installation, native_root, phase):
    assert host._worker is not None
    pid = host._worker.child.pid
    (receipt,) = host_maintenance_directory(installation.prefix).glob("mcp-activation-*.json")
    locator = Path(json.loads(receipt.read_bytes())["locator"])
    assert locator.parent.parent == native_root
    assert locator.is_socket()
    check, stop = None, None
    if phase == "handoff":
        check = _prepare_from_web(host, installation.prefix.parent, "check")
        stop = _prepare_from_web(host, installation.prefix.parent, "stop")
        assert _invoke(patcher, host, "stop", handoff=check) == 1  # Cross-action refusal before review.
    assert _invoke(patcher, host, "check", handoff=check) == 1  # No MCP readiness proof yet.
    assert host._worker.child.pid == pid
    assert _invoke(patcher, host, "stop", handoff=stop) == (1 if phase == "wrong-stop-ack" else 0)
    if phase == "wrong-stop-ack":
        assert host._worker.stopping
    else:
        if stop is not None:
            assert _invoke(patcher, host, "stop", handoff=stop) == 1
        with pytest.raises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)  # The exact direct child was reaped before the CLI returned.
        assert _invoke(patcher, host, "stop") == 0
    assert locator.is_socket()
    with pytest.raises(ConnectionRefusedError), socket.socket(socket.AF_UNIX) as client:
        client.connect(os.fspath(locator))


def _exercise_installed_cli(directory, phase):
    if phase.startswith("manual-"):
        _exercise_manual_install(directory, phase)
        return
    if phase.startswith("install-"):
        _exercise_install_handoff(directory, phase)
        return
    sdk, state = directory / "sdk", directory / "state"
    sdk.mkdir(mode=0o700)
    state.mkdir(mode=0o700)
    installation = InstalledMcpRuntime(sdk, state)
    plan = PrivateMcpPlan.capture(
        McpGeneration(_REGISTRATION, 4), "fixture-only", 3, time.monotonic() + 120, {"dispatch": {}}
    )
    registration = lifecycle.ForegroundMcpRegistration.capture(installation, plan, sdk_fingerprint="a" * 64)
    manager = _Manager("PID\tStatus\tLabel\n-\t0\tunrelated\n")
    host = lifecycle.ForegroundMcpLifecycle(registration, manager=manager)
    native_root = Path(os.confstr(65537)).resolve() / roots._locator_name()
    assert not native_root.exists()

    def forbidden_boot():
        pytest.fail("The foreground lifecycle booted the project kernel")

    try:
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(cli, "boot", forbidden_boot)
            patcher.setattr(roots, "_locator_name", lambda: native_root.name)
            _inject_lifecycle_race(patcher, phase, manager, registration.native.name)
            handoff = _prepare_from_web(host, directory, "start") if phase.startswith("handoff") else None
            assert _invoke(patcher, host, "start", handoff=handoff) == 1
            if phase in _START_REFUSED:
                assert host._worker is None
                if phase == "wrong-start-ack":
                    (locator,) = native_root.glob("g-*/s")
                    with pytest.raises(ConnectionRefusedError), socket.socket(socket.AF_UNIX) as client:
                        client.connect(os.fspath(locator))
                else:
                    assert not native_root.exists()
            else:
                _exercise_running(patcher, host, installation, native_root, phase)
            assert manager.commands
            assert all(command == ["/bin/launchctl", "list"] for command in manager.commands)
    finally:
        host.close()
        if native_root.is_dir():
            shutil.rmtree(native_root)


class _InstallPlanner:
    def __init__(self, reviewer):
        self.reviewer = reviewer

    def status(self, _registration):
        return McpLifecycleObservation(host="observed", actions=("install",))

    def prepare(self, action, registration, expires_at):
        assert action == "install"
        return self.reviewer.prepare(registration, expires_at), "terminal"


def _exercise_install_handoff(directory, phase):
    config = directory / "fixture-client.json"
    config.write_text("{}\n")

    def snapshot(registration):
        assert registration == _REGISTRATION
        info = config.stat()
        return {
            "client": "fictional-client",
            "config": str(config),
            "config_identity": [info.st_dev, info.st_ino],
            "config_preimage": hashlib.sha256(config.read_bytes()).hexdigest(),
            "sdk_fingerprint": "a" * 64,
            "grant": "fixture-only",
            "grant_revision": 3,
            "generation": 4,
        }

    reviewer = McpInstallReview(snapshot)

    plan = _prepare_from_web(_InstallPlanner(reviewer), directory, "install", validate_install=reviewer.validate)
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(sys, "argv", ["hz", "system", "mcp", "review-install", _REGISTRATION, "--handoff", plan])
        with pytest.raises(SystemExit) as result:
            cli.main(host_mcp_install_review=reviewer)
    assert result.value.code == (0 if phase == "install-accepted" else 1)
    expected = '{"updated": true}\n' if phase == "install-changed" else "{}\n"
    assert config.read_text() == expected


def _manual_target(directory, phase):
    client = {"manual-codex": McpClient.CODEX, "manual-cursor": McpClient.CURSOR}.get(phase, McpClient.CLAUDE)
    return ClientConfigContext(client, directory / "home", directory / "workspace")


def _exercise_manual_install(directory, phase):
    context = _manual_target(directory, phase)
    context.path.parent.mkdir(parents=True)
    context.workspace.mkdir()
    binding = ClientBridgeBinding(_REGISTRATION, directory / "interpreter", directory / "bridge.py")
    binding.interpreter.write_bytes(b"fixture interpreter")
    binding.interpreter.chmod(0o700)
    binding.bridge.write_bytes(b"fixture bridge")
    pins = PinnedClientBridge(
        binding,
        hashlib.sha256(binding.interpreter.read_bytes()).hexdigest(),
        hashlib.sha256(binding.bridge.read_bytes()).hexdigest(),
    )
    fixture = runpy.run_path(str(Path(__file__).with_name("mcp_client_config_host_test.py")))
    authority = {"grant": "fixture-only", "grant_revision": 3, "generation": 4, "sdk_fingerprint": "a" * 64}
    observed_edit = False

    def observe(config, bridge):
        nonlocal observed_edit
        assert config == context
        assert bridge == binding
        assert context.client is not McpClient.CURSOR
        observed_edit = binding.name in context.path.read_text()
        if phase == "manual-native-source" and observed_edit:
            return fixture["_native"](config, bridge, installed=True, source=directory / "another.json")
        return fixture["_fixture_observe"](config, bridge)

    @contextmanager
    def fence(expected):
        # Model revocation exactly at the protected publication boundary, after the last snapshot.
        if phase == "manual-revoked" and observed_edit:
            authority["grant_revision"] += 1
        if expected != arguments_hash(authority) or (directory / "revoke-session").exists():
            message = "The fixture grant changed."
            raise HeinzelError(message)
        yield

    def clock():
        return time.monotonic() + (301 if (directory / "expire-session").exists() else 0)

    def snapshot(registration):
        assert registration == _REGISTRATION
        return dict(authority)

    def forbidden_boot():
        pytest.fail("The manual Install route booted the project kernel")

    reviewer = McpInstallReview(snapshot, manual=ManualInstallAuthority(context, pins, observe, fence), clock=clock)
    baseline = "fixture = true\n" if context.client is McpClient.CODEX else '{"fixture": true}\n'
    if phase == "manual-collision":
        baseline = json.dumps({context.table: {binding.name: {"command": "unrelated"}}})
    context.path.write_text(baseline)
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(cli, "boot", forbidden_boot)
        _run_manual_fixture(reviewer, context, directory, phase, patcher)


def _run_manual_fixture(reviewer, context, directory, phase, patcher):
    if phase.startswith("manual-web"):
        _exercise_web_manual_install(reviewer, context, directory, phase, patcher)
    else:
        handoff = _prepare_manual_fixture(reviewer, phase)
        _check_manual_invocations(reviewer, directory, phase, handoff, patcher)


def _check_manual_invocations(reviewer, directory, phase, handoff, patcher):
    patcher.setattr(sys, "argv", ["hz", "system", "mcp", "review-install", _REGISTRATION, "--handoff", handoff])
    if phase == "manual-silent-expiry":
        pending = reviewer._session.pending
        assert pending is not None
        (directory / "handoff-deadline").write_text(str(pending.deadline))
    assert _invoke_manual_install(reviewer) == (0 if phase in _MANUAL_SUCCESS else 1)
    assert _invoke_manual_install(reviewer) == 1
    if phase == "manual-silent-expiry":
        assert reviewer.prepare(_REGISTRATION, int(time.time()) + 120) != handoff


def _exercise_web_manual_install(reviewer, context, directory, phase, patcher):
    with _planning_client(_InstallPlanner(reviewer), directory, validate_install=reviewer.validate) as client:
        prepare = f"/api/mcp/lifecycle/{_REGISTRATION}/prepare?action=install"
        response = client.post(prepare)
        assert response.status_code == 200
        assert str(directory) not in response.text
        plan = response.json()
        assert plan["session"] == "terminal"
        resolve = "/api/mcp/handoffs/" + plan["id"]
        pending = reviewer._session.pending
        assert pending is not None
        for _ in range(2):
            assert client.get(resolve).json() == plan
            assert reviewer._session.pending is pending
        assert client.post(prepare).status_code == 409
        if phase in {"manual-web", "manual-web-cancelled"}:
            _check_manual_invocations(reviewer, directory, phase, plan["handoff"], patcher)
            assert reviewer._session.pending is None
        else:
            _change_web_fixture(context, directory, phase)
            assert reviewer._session.pending is pending
        assert client.get(resolve).status_code == 409


def _change_web_fixture(context, directory, phase):
    match phase:
        case "manual-web-preimage":
            replacement = context.path.with_suffix(".replacement")
            replacement.write_bytes(context.path.read_bytes())
            replacement.replace(context.path)
        case "manual-web-expired":
            (directory / "expire-session").touch()
        case "manual-web-revoked":
            (directory / "revoke-session").touch()
        case "manual-web-pins":
            (directory / "bridge.py").write_bytes(b"replacement fixture bridge")
        case "manual-web-scope":
            context.workspace.rename(directory / "old-workspace")
            context.workspace.mkdir()
        case _:
            pytest.fail(f"Unknown fixture phase {phase}")


def _invoke_manual_install(reviewer):
    with pytest.raises(SystemExit) as result:
        cli.main(host_mcp_install_review=reviewer)
    return result.value.code


def _prepare_manual_fixture(reviewer, phase):
    expiry = int(time.time()) + (3 if phase == "manual-silent-expiry" else 120)
    if phase in {"manual-collision", "manual-cursor"}:
        with pytest.raises(HeinzelError):
            reviewer.prepare(_REGISTRATION, expiry)
        return f"{expiry}.{'b' * 64}"
    handoff = reviewer.prepare(_REGISTRATION, expiry)
    with pytest.raises(HeinzelError, match="session is unavailable"):
        reviewer.prepare(_REGISTRATION, expiry)
    return handoff


def _inject_lifecycle_race(patcher, phase, manager, name):
    if phase == "manager-changed":
        command = manager.command

        def changed(arguments):
            if manager.commands:
                manager.stdout += f"99\t0\t{name}\n"
            return command(arguments)

        patcher.setattr(manager, "command", changed)
    if phase in {"wrong-start-ack", "wrong-stop-ack"}:
        reply = lifecycle._reply
        wrong = b"T" if phase == "wrong-start-ack" else b"S"

        def wrong_ack(channel, binding, status):
            reply(channel, binding, b"U" if status == wrong else status)

        patcher.setattr(lifecycle, "_reply", wrong_ack)


def _read_review(terminal):
    deadline = time.monotonic() + 2
    output = bytearray()
    while b"\r\n> " not in output:
        remaining = deadline - time.monotonic()
        assert remaining > 0
        assert select.select([terminal], [], [], remaining)[0]
        output.extend(os.read(terminal, 4096))
    return bytes(output)


def _answer_reviews(terminal, directory, phase):
    actions = ["start"]
    if phase in {"success", "wrong-stop-ack", "handoff"}:
        actions += ["check", "stop"]
    if phase in {"success", "handoff"}:
        actions += ["stop"]
    for action in actions:
        prompt = _read_review(terminal)
        assert b"Review this exact MCP foreground lifecycle action" in prompt
        assert f'"action": "{action}"'.encode() in prompt
        assert b'"registration": "fixture-foreground"' in prompt
        assert b'"grant_revision": 3' in prompt
        match = re.search(rb"Type exactly: (approve MCP [0-9a-f]{8})\r\n", prompt)
        assert match is not None
        if phase in {"changed", "handoff-changed"}:
            (directory / "state").rename(directory / "old-state")
            (directory / "state").mkdir(mode=0o700)
        os.write(terminal, (b"no" if phase == "decline" else match[1]) + b"\n")


@contextmanager
def _installed_terminal(directory, phase):
    master, terminal = pty.openpty()
    settings = termios.tcgetattr(terminal)
    # An unread answer echo can block Darwin PTY close while communicate waits for exit.
    settings[3] &= ~termios.ECHO
    termios.tcsetattr(terminal, termios.TCSANOW, settings)
    try:
        with subprocess.Popen(
            [sys.executable, "-I", "-c", _LAUNCHER, __file__, os.fspath(directory), phase],
            stdin=terminal,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        ) as process:
            os.close(terminal)
            terminal = -1
            try:
                yield master, process
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


@pytest.mark.skipif(sys.platform != "darwin", reason="Exercises a real Darwin locator and owned foreground child")
@pytest.mark.parametrize(
    "phase",
    [
        "success",
        "decline",
        "changed",
        "manager-changed",
        "wrong-start-ack",
        "wrong-stop-ack",
        "handoff",
        "handoff-changed",
    ],
)
def test_installed_foreground_cli_reviews_action_and_proves_owned_worker_exit(short_socket_root, phase):
    with _installed_terminal(short_socket_root, phase) as (master, process):
        _answer_reviews(master, short_socket_root, phase)
        stdout, stderr = process.communicate(timeout=4)
        assert process.returncode == 0, stderr.decode()
    if phase in _START_REFUSED:
        assert stdout == b""
        assert stderr.endswith(b"The reviewed MCP lifecycle action was declined or unavailable.\n")
    else:
        states = [json.loads(line) for line in stdout.splitlines()]
        assert [(state["action"], state["worker"], state["transport"]) for state in states[:2]] == [
            ("start", "running", "reachable"),
            ("check", "running", "reachable"),
        ]
        assert all(state["mcp_readiness"] == "unverified" and state["manager"] == "absent" for state in states)
        assert [state["worker"] for state in states[2:]] == (
            ["stop-incomplete"] if phase == "wrong-stop-ack" else ["stopped", "stopped"]
        )


@pytest.mark.parametrize("action", ["start", "check", "stop", "review-install"])
def test_standalone_cli_cannot_assert_installed_host_authority(tmp_path, action):
    arguments = [sys.executable, "-I", "-m", "heinzel", "system", "mcp", action, _REGISTRATION]
    error = b"MCP lifecycle requires a registration from the trusted installed host.\n"
    if action == "review-install":
        arguments += ["--handoff", f"{int(time.time()) + 100}.{'a' * 64}"]
        error = b"The local MCP Install review was declined or unavailable.\n"
    result = subprocess.run(
        arguments,
        cwd=tmp_path,
        env={"HZ_MCP_HOST": "true", "HZ_STATE_DIR": os.fspath(tmp_path / "forged")},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=2,
        check=False,
    )
    assert (result.returncode, result.stdout, result.stderr) == (1, b"", error)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("phase", ["install-accepted", "install-declined", "install-changed"])
def test_web_install_handoff_reaches_exact_local_review_only(short_socket_root, phase):
    with _installed_terminal(short_socket_root, phase) as (master, process):
        prompt = _read_review(master)
        assert b'"action": "install"' in prompt
        assert b'"review_mode": "review-only"' in prompt
        assert b'"config_preimage":' in prompt
        assert b'"grant_revision": 3' in prompt
        match = re.search(rb"Type exactly: (approve MCP [0-9a-f]{8})\r\n", prompt)
        assert match is not None
        if phase == "install-changed":
            (short_socket_root / "fixture-client.json").write_text('{"updated": true}\n')
        os.write(master, (b"no" if phase == "install-declined" else match[1]) + b"\n")
        stdout, stderr = process.communicate(timeout=4)
        assert process.returncode == 0, stderr.decode()
    if phase == "install-accepted":
        assert json.loads(stdout) == {"review": "accepted", "installation": "not-applied"}
    else:
        assert stdout == b""


def _manual_prompt(terminal, mode):
    prompt = _read_review(terminal).decode().replace("\r\n", "\n")
    _, _, content = prompt.partition("Review this exact MCP foreground lifecycle action:\n")
    plan, _, challenge = content.partition("\n\nType exactly: ")
    payload = json.loads(plan)
    assert payload["review_mode"] == mode
    assert payload["registration"] == _REGISTRATION
    match = re.fullmatch(r"(approve MCP [0-9a-f]{8})\n> ", challenge)
    assert match is not None
    return payload, match[1].encode() + b"\n"


@pytest.mark.parametrize(
    "phase",
    [
        "manual-claude",
        "manual-codex",
        "manual-collision",
        "manual-replaced",
        "manual-cursor",
        "manual-cancelled",
        "manual-expired",
        "manual-revoked",
        "manual-native-source",
        "manual-unrelated-edit",
        "manual-scope-replaced",
        "manual-web",
        "manual-web-cancelled",
    ],
)
def test_foreground_cli_verifies_only_one_reviewed_manual_install(short_socket_root, phase):
    context = _manual_target(short_socket_root, phase)
    with _installed_terminal(short_socket_root, phase) as (terminal, process):
        if phase not in {"manual-collision", "manual-cursor"}:
            approved, answer = _manual_prompt(terminal, "manual-install")
            assert approved["client"] == context.client.value
            assert approved["authority"]["grant_revision"] == 3
            baseline = context.path.read_text()
            if phase == "manual-replaced":
                replacement = context.path.with_suffix(".replacement")
                replacement.write_text(baseline)
                replacement.replace(context.path)
            os.write(terminal, answer)
            if phase != "manual-replaced":
                _complete_manual_fixture(terminal, context, approved, baseline, phase)
        stdout, stderr = process.communicate(timeout=4)
        assert process.returncode == 0, stderr.decode()
    if phase in _MANUAL_SUCCESS:
        assert json.loads(stdout) == {"configuration": "verified", "mcp_readiness": "unverified"}
    else:
        assert not stdout
    if phase in {"manual-replaced", "manual-cancelled", "manual-scope-replaced", "manual-web-cancelled"}:
        assert context.path.read_text() == '{"fixture": true}\n'


def _complete_manual_fixture(terminal, context, approved, baseline, phase):
    edit, answer = _manual_prompt(terminal, "verify-manual-edit")
    assert edit["installation"] == "not-applied"
    assert edit["session"] == approved["session"]
    assert edit["path"] == str(context.path)
    assert edit["table"] == context.table
    assert context.path.read_text() == baseline
    if phase in {"manual-cancelled", "manual-web-cancelled"}:
        os.write(terminal, b"no\n")
        return
    if phase == "manual-scope-replaced":
        context.workspace.rename(context.workspace.with_name("previous-workspace"))
        context.workspace.mkdir()
    # Only the test driver writes the fixture config, exactly as a separate manual edit would.
    elif context.client is McpClient.CODEX:
        context.path.write_text(baseline + edit["entry"])
    else:
        data = json.loads(baseline) | json.loads(edit["entry"])
        if phase == "manual-unrelated-edit":
            data["fixture"] = False
        context.path.write_text(json.dumps(data))
    if phase == "manual-expired":
        (context.home.parent / "expire-session").touch()
    os.write(terminal, answer)


def test_silent_manual_confirmation_exits_at_the_retained_deadline(short_socket_root):
    phase = "manual-silent-expiry"
    with _installed_terminal(short_socket_root, phase) as (terminal, process):
        approved, answer = _manual_prompt(terminal, "manual-install")
        os.write(terminal, answer)
        confirm, _ = _manual_prompt(terminal, "verify-manual-edit")
        assert confirm["session"] == approved["session"]
        deadline = float((short_socket_root / "handoff-deadline").read_text())
        # Keep the PTY open and silent. No input, hangup or injected clock can end this wait.
        stdout, stderr = process.communicate(timeout=max(0, deadline - time.monotonic()) + 1)
        assert process.returncode == 0, stderr.decode()
    assert time.monotonic() >= deadline
    assert stdout == b""
    assert stderr.count(b"The local MCP Install review was declined or unavailable.") == 2
    assert _manual_target(short_socket_root, phase).path.read_text() == '{"fixture": true}\n'


@pytest.mark.parametrize(
    "phase", ["manual-web-preimage", "manual-web-expired", "manual-web-revoked", "manual-web-pins", "manual-web-scope"]
)
def test_web_install_resolution_refuses_changed_or_unavailable_manual_plan(short_socket_root, phase):
    with _installed_terminal(short_socket_root, phase) as (_, process):
        stdout, stderr = process.communicate(timeout=4)
        assert process.returncode == 0, stderr.decode()
    assert stdout == b""


def test_native_manager_preflight_observes_only_exact_namespace():
    mac = McpNativePlan("darwin", "heinzel.test.mcp")
    header = "PID\tStatus\tLabel\n"
    assert mac.observe(_Manager(header + "-\t0\theinzel.test.mcp.other\n")) == "absent"
    assert mac.observe(_Manager(header + "55\t0\theinzel.test.mcp\n")) == "conflict"
    assert mac.observe(_Manager(header + "malformed output\n")) == "unavailable"
    linux = McpNativePlan("linux", "hz-mcp-fixture.service")
    absent = (
        "Id=hz-mcp-fixture.service\nLoadState=not-found\nFragmentPath=\nDropInPaths=\nMainPID=0\nControlPID=0\nJob=\n"
    )
    manager = _Manager(absent)
    assert linux.observe(manager) == "absent"
    assert manager.commands[0][:4] == ["/usr/bin/systemctl", "--user", "show", linux.name]
    assert linux.observe(_Manager(absent.replace("MainPID=0", "MainPID=55"))) == "conflict"
    assert linux.observe(_Manager(absent + "Id=foreign.service\n")) == "unavailable"
