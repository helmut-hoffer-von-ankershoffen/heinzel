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


def _codex_config(context, binding, *, installed, source=None):
    metadata = {"type": "user", "file": str(source or context.path), "profile": None}
    version = "sha256:" + "3" * 64
    config = {"mcp_servers": {binding.name: binding.entry(context.client)}} if installed else {}
    origins = (
        {
            f"mcp_servers.{binding.name}.{key}": {"name": metadata, "version": version}
            for key in ("command", "args.0", "args.1", "args.2")
        }
        if installed
        else {}
    )
    return {
        "config": config,
        "origins": origins,
        "layers": [{"name": metadata, "version": version, "config": config, "disabledReason": None}],
    }


def _native(context, binding, *, installed, source=None):
    if context.client is McpClient.CLAUDE:
        if not installed:
            return NativeInspection(
                1, f'No MCP server named "{binding.name}". Run `claude mcp add` to add one.', "", context=context
            )
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
            context=context,
        )
    if not installed:
        return NativeInspection(
            1,
            "",
            f"Error: No MCP server named '{binding.name}' found.",
            context=context,
            codex_config_read=json.dumps(_codex_config(context, binding, installed=False)),
        )
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
        context=context,
        codex_config_read=json.dumps(_codex_config(context, binding, installed=True, source=source)),
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
        (
            replace(native, source=None)
            if client is McpClient.CLAUDE
            else replace(native, source=None, codex_config_read=None)
        ),
        replace(native, source=workspace / "override"),
        replace(native, context=replace(context, workspace=workspace.parent)),
        replace(native, stdout=native.stdout.replace("-I", "-E")),
        replace(native, stdout=native.stdout.replace(pin.binding.name, "other")),
        replace(native, stdout="x" * (129 * 1024)),
    ):
        assert observe_client_config(context, pin.binding, changed).status is NativeConfigStatus.UNAVAILABLE


def test_cursor_listing_is_not_effective_config_evidence(profile):
    home, workspace, pin = profile
    context = ClientConfigContext(McpClient.CURSOR, home, workspace)
    row = NativeInspection(0, f"{pin.binding.name} connected", "", context.path, context=context)
    assert observe_client_config(context, pin.binding, row).status is NativeConfigStatus.UNAVAILABLE

    def probe(*_args):
        pytest.fail("an unsupported inspection must not start the vendor client")

    flow = HostClientConfigWorkflow(context, pin, observe=probe, approve_local=lambda _review: True)
    with pytest.raises(HeinzelError, match="native MCP config is unavailable"):
        flow.prepare(ClientConfigAction.INSTALL)


@pytest.mark.parametrize(
    "problem", ["same-argv-project", "project-origin", "wrong-profile", "wrong-version", "missing-arg", "no-layers"]
)
def test_codex_get_needs_matching_complete_native_origins(profile, problem):
    home, workspace, pin = profile
    context = ClientConfigContext(McpClient.CODEX, home, workspace)
    binding = pin.binding
    probe = _native(context, binding, installed=True)
    result = _codex_config(context, binding, installed=True)
    key = f"mcp_servers.{binding.name}.args.2"
    if problem == "same-argv-project":
        result["layers"].append(
            {
                "name": {"type": "project", "dotCodexFolder": str(workspace / ".codex")},
                "config": {"mcp_servers": {binding.name: binding.entry(McpClient.CODEX)}},
                "version": "sha256:" + "2" * 64,
            }
        )
    elif problem == "project-origin":
        result["origins"][key]["name"] = {"type": "project", "dotCodexFolder": str(workspace / ".codex")}
    elif problem == "wrong-profile":
        result["layers"][0]["name"]["profile"] = "work"
    elif problem == "wrong-version":
        result["origins"][key]["version"] = "sha256:" + "4" * 64
    elif problem == "missing-arg":
        del result["origins"][key]
    else:
        result["layers"] = None
    rejected = replace(probe, codex_config_read=json.dumps(result))
    assert observe_client_config(context, binding, rejected).status is NativeConfigStatus.UNAVAILABLE


def test_codex_absence_requires_the_same_standard_profile_layer(profile):
    home, workspace, pin = profile
    context = ClientConfigContext(McpClient.CODEX, home, workspace)
    binding = pin.binding
    native = _native(context, binding, installed=False)
    assert observe_client_config(context, binding, native).status is NativeConfigStatus.ABSENT
    missing = replace(native, codex_config_read=None)
    assert observe_client_config(context, binding, missing).status is NativeConfigStatus.UNAVAILABLE
    alternate = _codex_config(context, binding, installed=False, source=home / "alt.toml")
    mismatched = replace(native, codex_config_read=json.dumps(alternate))
    assert observe_client_config(context, binding, mismatched).status is NativeConfigStatus.UNAVAILABLE


def test_host_refuses_codex_project_config_before_probe(profile):
    home, workspace, pin = profile
    context = ClientConfigContext(McpClient.CODEX, home, workspace)
    project_config = workspace.parent / ".codex" / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text(f'[mcp_servers.{pin.binding.name}]\ncommand = "/bin/false"\n')

    def probe(*_args):
        pytest.fail("project override reached the native client")

    flow = HostClientConfigWorkflow(context, pin, observe=probe, approve_local=lambda _review: True)
    with pytest.raises(HeinzelError, match="project MCP configuration"):
        flow.prepare(ClientConfigAction.INSTALL)
