import hashlib
import json
import tomllib
from dataclasses import replace

import pytest

from heinzel.heads.mcp._client_config import (
    ClientBridgeBinding,
    ClientConfigAction,
    ClientConfigContext,
    McpClient,
    NativeConfigStatus,
)
from heinzel.heads.mcp._client_config_files import PinnedClientBridge
from heinzel.heads.mcp._client_config_host import HostClientConfigWorkflow
from heinzel.heads.mcp._client_config_native import NativeInspection, observe_client_config
from heinzel.kernel import HeinzelError

pytestmark = pytest.mark.unit


@pytest.fixture
def profile(tmp_path):
    root = tmp_path.resolve()
    home = root / "home"
    workspace = root / "project" / "work"
    home.mkdir()
    workspace.mkdir(parents=True)
    interpreter = root / "python"
    bridge = root / "bridge.py"
    interpreter.write_bytes(b"fixture interpreter")
    interpreter.chmod(0o700)
    bridge.write_bytes(b"fixture bridge")
    binding = ClientBridgeBinding("fixture-registration", interpreter, bridge)
    pins = PinnedClientBridge(
        binding, hashlib.sha256(interpreter.read_bytes()).hexdigest(), hashlib.sha256(bridge.read_bytes()).hexdigest()
    )
    return home, workspace, pins


def _native(context, binding, *, installed, source=None):
    if context.client is McpClient.CLAUDE:
        if not installed:
            return NativeInspection(1, f'No MCP server named "{binding.name}". Run `claude mcp add` to add one.', "")
        return NativeInspection(
            0,
            (
                f"{binding.name}:\n"
                "  Scope: User config (available in all your projects)\n"
                "  Status: ✘ Failed to connect\n"
                "  Issue: CONNECTION_CLOSED: Connection closed\n"
                "  Type: stdio\n"
                f"  Command: {binding.interpreter}\n"
                f"  Args: -I -S {binding.bridge}\n"
            ),
            "",
            source or context.path,
        )
    if not installed:
        return NativeInspection(1, "", f"Error: No MCP server named '{binding.name}' found.")
    return NativeInspection(
        0,
        json.dumps(
            {
                "name": binding.name,
                "enabled": True,
                "disabled_reason": None,
                "transport": {
                    "type": "stdio",
                    **binding.entry(McpClient.CODEX),
                    "env": None,
                    "env_vars": [],
                    "cwd": None,
                },
                "enabled_tools": None,
                "disabled_tools": None,
                "startup_timeout_sec": None,
                "tool_timeout_sec": None,
            }
        ),
        "",
        source or context.path,
    )


def _fixture_observe(context, binding):
    if not context.path.exists():
        return _native(context, binding, installed=False)
    raw = context.path.read_text()
    document = (tomllib.loads if context.client is McpClient.CODEX else json.loads)(raw)
    return _native(context, binding, installed=binding.name in document.get(context.table, {}))


@pytest.mark.parametrize("client", [McpClient.CLAUDE, McpClient.CODEX])
def test_exact_host_review_then_manual_install_check_and_remove(profile, client):
    home, workspace, pin = profile
    context = ClientConfigContext(client, home, workspace)
    reviews = []
    flow = HostClientConfigWorkflow(
        context, pin, observe=_fixture_observe, approve_local=lambda review: not reviews.append(review)
    )
    install = flow.prepare(ClientConfigAction.INSTALL)
    assert not reviews
    with pytest.raises(HeinzelError, match="review is unavailable"):
        flow.verify(install)
    with pytest.raises(HeinzelError, match="review is unavailable"):
        flow.review("invalid-handle")
    edit = flow.review(install)
    assert not context.path.exists()
    assert edit.install_snippet is not None
    assert edit.path == context.path
    assert edit.table == context.table
    assert edit.server_name == pin.binding.name
    assert reviews[0].identifier == install
    assert reviews[0].command == str(pin.binding.interpreter)
    assert reviews[0].arguments == ("-I", "-S", str(pin.binding.bridge))
    with pytest.raises(HeinzelError, match="review is unavailable"):
        flow.review(install)
    context.path.parent.mkdir(exist_ok=True)
    context.path.write_text(edit.install_snippet)
    assert flow.verify(install) is ClientConfigAction.INSTALL
    flow.check()
    with pytest.raises(HeinzelError, match="review is unavailable"):
        flow.verify(install)
    uninstall = flow.prepare(ClientConfigAction.UNINSTALL)
    removal = flow.review(uninstall)
    assert reviews[0].digest != reviews[1].digest
    assert removal.install_snippet is None
    assert context.path.read_text() == edit.install_snippet
    context.path.write_text("" if client is McpClient.CODEX else "{}")
    assert flow.verify(uninstall) is ClientConfigAction.UNINSTALL
    with pytest.raises(HeinzelError, match="bridge is absent"):
        flow.check()


def test_deny_and_expire_are_one_use(profile):
    home, workspace, pin = profile
    context = ClientConfigContext(McpClient.CLAUDE, home, workspace)
    clock = [10.0]
    approvals = []

    def decline(review):
        approvals.append(review)
        return False

    flow = HostClientConfigWorkflow(
        context, pin, observe=_fixture_observe, approve_local=decline, clock=lambda: clock[0]
    )
    pending = flow.prepare(ClientConfigAction.INSTALL)
    with pytest.raises(HeinzelError, match="review is unavailable"):
        flow.review(pending)
    with pytest.raises(HeinzelError, match="review is unavailable"):
        flow.review(pending)
    assert len(approvals) == 1
    pending = flow.prepare(ClientConfigAction.INSTALL)
    clock[0] += 301
    with pytest.raises(HeinzelError, match="review is unavailable"):
        flow.review(pending)
    assert len(approvals) == 1


def test_baseline_inode_changed_after_review_is_refused(profile):
    home, workspace, pin = profile
    context = ClientConfigContext(McpClient.CLAUDE, home, workspace)
    context.path.write_text("{}")

    def replace_file(_review):
        replacement = home / "replacement.json"
        replacement.write_text("{}")
        replacement.replace(context.path)
        return True

    flow = HostClientConfigWorkflow(context, pin, observe=_fixture_observe, approve_local=replace_file)
    pending = flow.prepare(ClientConfigAction.INSTALL)
    with pytest.raises(HeinzelError, match="review is unavailable"):
        flow.review(pending)
    assert context.path.read_text() == "{}"


@pytest.mark.parametrize("change", ["contents", "symlink", "permissions", "missing", "interpreter"])
def test_pin_change_refuses_before_native_inspection(profile, change):
    home, workspace, pin = profile
    context = ClientConfigContext(McpClient.CLAUDE, home, workspace)
    inspected = []
    flow = HostClientConfigWorkflow(
        context, pin, observe=lambda *args: inspected.append(args), approve_local=lambda _review: True
    )
    bridge = pin.binding.bridge
    if change == "contents":
        bridge.write_bytes(b"replacement")
    elif change == "permissions":
        bridge.chmod(0o666)
    elif change == "interpreter":
        pin.binding.interpreter.chmod(0o600)
    else:
        bridge.unlink()
        if change == "symlink":
            bridge.symlink_to(pin.binding.interpreter)
    with pytest.raises(HeinzelError, match="pinned bridge file"):
        flow.prepare(ClientConfigAction.INSTALL)
    assert not inspected


@pytest.mark.parametrize("override", ["ancestor", "claude-local", "config-symlink", "wrong-command"])
def test_host_refuses_competing_or_unsafe_config_before_probe(profile, override):
    home, workspace, pin = profile
    context = ClientConfigContext(McpClient.CLAUDE, home, workspace)
    if override == "ancestor":
        (workspace.parent / ".mcp.json").write_text("{}")
    elif override == "claude-local":
        context.path.write_text(
            json.dumps(
                {"projects": {str(workspace): {"mcpServers": {pin.binding.name: {"url": "https://example.test"}}}}}
            )
        )
    elif override == "config-symlink":
        context.path.symlink_to(pin.binding.bridge)
    else:
        context.path.write_text(json.dumps({"mcpServers": {pin.binding.name: {"command": "/bin/false"}}}))

    def probe(*_args):
        pytest.fail("unsafe profile reached the native client")

    flow = HostClientConfigWorkflow(context, pin, observe=probe, approve_local=lambda _review: True)
    with pytest.raises(HeinzelError, match=r"project MCP configuration|pinned bridge file|different MCP entry"):
        flow.prepare(ClientConfigAction.INSTALL)


def test_native_initialization_forces_fresh_baseline(profile):
    home, workspace, pin = profile
    context = ClientConfigContext(McpClient.CLAUDE, home, workspace)

    def initialize(config, binding):
        config.path.write_text('{"firstStartVersion":"fixture"}')
        return _native(config, binding, installed=False)

    flow = HostClientConfigWorkflow(context, pin, observe=initialize, approve_local=lambda _review: True)
    with pytest.raises(HeinzelError, match="changed during native inspection"):
        flow.prepare(ClientConfigAction.INSTALL)


@pytest.mark.parametrize("client", [McpClient.CLAUDE, McpClient.CODEX])
def test_unverified_native_source_and_modified_argv_are_unavailable(profile, client):
    home, workspace, pin = profile
    context = ClientConfigContext(client, home, workspace)
    native = _native(context, pin.binding, installed=True)
    assert observe_client_config(context, pin.binding, native).status is NativeConfigStatus.SELECTED
    for changed in (
        replace(native, source=None),
        replace(native, source=workspace / "override"),
        replace(native, stdout=native.stdout.replace("-I", "-E")),
        replace(native, stdout=native.stdout.replace(pin.binding.name, "other")),
        replace(native, stdout="x" * (129 * 1024)),
    ):
        assert observe_client_config(context, pin.binding, changed).status is NativeConfigStatus.UNAVAILABLE


def test_cursor_listing_is_not_effective_config_evidence(profile):
    home, workspace, pin = profile
    context = ClientConfigContext(McpClient.CURSOR, home, workspace)
    row = NativeInspection(0, f"{pin.binding.name} connected", "", context.path)
    assert observe_client_config(context, pin.binding, row).status is NativeConfigStatus.UNAVAILABLE

    def probe(*_args):
        pytest.fail("an unsupported inspection must not start the vendor client")

    flow = HostClientConfigWorkflow(context, pin, observe=probe, approve_local=lambda _review: True)
    with pytest.raises(HeinzelError, match="native MCP config is unavailable"):
        flow.prepare(ClientConfigAction.INSTALL)
