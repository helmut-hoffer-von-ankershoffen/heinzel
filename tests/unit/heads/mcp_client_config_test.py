import json
import logging
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from heinzel.heads.mcp._client_config import (
    ClientBridgeBinding,
    ClientConfigAction,
    ClientConfigContext,
    McpClient,
    NativeClientConfig,
    NativeConfigStatus,
    plan_manual_client_config,
)
from heinzel.kernel import HeinzelError
from heinzel.kernel._logging import record_fields

pytestmark = pytest.mark.unit


@pytest.fixture(params=list(McpClient))
def context(request):
    return ClientConfigContext(request.param, Path("/fixture/user"), Path("/fixture/workspace"))


@pytest.fixture
def binding():
    return ClientBridgeBinding("registration-fixture", Path("/fixture/sdk/bin/python"), Path("/fixture/bridge.py"))


def _absent(context, binding):
    return NativeClientConfig(context, binding.name, NativeConfigStatus.ABSENT)


def _selected(context, binding):
    return NativeClientConfig(
        context, binding.name, NativeConfigStatus.SELECTED, context.path, binding.entry(context.client)
    )


def _plan(context, binding, before=None):
    return plan_manual_client_config(ClientConfigAction.INSTALL, context, binding, before, _absent(context, binding))


def _existing(context):
    if context.client is McpClient.CODEX:
        return b'other = true\n[mcp_servers.unrelated]\nurl = "https://example.test/mcp"\n'
    return b'{"other":true,"mcpServers":{"unrelated":{"url":"https://example.test/mcp"}}}'


def _insert(context, plan, before):
    if context.client is McpClient.CODEX:
        return before + plan.install_snippet().encode()
    data = json.loads(before)
    data.setdefault(context.table, {}).update(json.loads(plan.install_snippet())[context.table])
    return json.dumps(data).encode()


def test_vendor_config_round_trip_preserves_unrelated_fields(context, binding):
    before = _existing(context)
    install = _plan(context, binding, before)
    install.require_current(before, _absent(context, binding))
    installed = _insert(context, install, before)
    parser = tomllib.loads if context.client is McpClient.CODEX else json.loads
    assert parser(installed.decode())[context.table][binding.name] == binding.entry(context.client)
    assert binding.entry(context.client)["args"] == ["-I", "-S", "/fixture/bridge.py"]
    install.verify(before, installed, _selected(context, binding))
    remove = plan_manual_client_config(
        ClientConfigAction.UNINSTALL, context, binding, installed, _selected(context, binding)
    )
    remove.require_current(installed, _selected(context, binding))
    remove.verify(installed, before, _absent(context, binding))
    assert remove.digest != install.digest
    with pytest.raises(HeinzelError, match="removal"):
        remove.install_snippet()


@pytest.mark.parametrize(
    ("client", "relative"),
    [
        (McpClient.CLAUDE, ".claude.json"),
        (McpClient.CODEX, ".codex/config.toml"),
        (McpClient.CURSOR, ".cursor/mcp.json"),
    ],
)
def test_default_user_profile_target(client, relative):
    context = ClientConfigContext(client, Path("/fixture/user"), Path("/fixture/workspace"))
    assert context.path == Path("/fixture/user") / relative


def test_missing_file_can_be_created_by_manual_edit(context, binding):
    plan = _plan(context, binding)
    plan.verify(None, plan.install_snippet().encode(), _selected(context, binding))
    with pytest.raises(HeinzelError, match="changed"):
        plan.require_current(b"", _absent(context, binding))
    with pytest.raises(HeinzelError, match="changed"):
        plan.require_current(b"missing", _absent(context, binding))


def test_refuses_collision_in_any_scope(context, binding):
    before = _existing(context)
    installed = _insert(context, _plan(context, binding, before), before)
    with pytest.raises(HeinzelError, match="already exists"):
        _plan(context, binding, installed)
    project_override = replace(_selected(context, binding), source=context.workspace / "project-config")
    with pytest.raises(HeinzelError, match="different entry"):
        plan_manual_client_config(ClientConfigAction.INSTALL, context, binding, before, project_override)
    with pytest.raises(HeinzelError, match="different entry"):
        plan_manual_client_config(ClientConfigAction.UNINSTALL, context, binding, installed, project_override)


def test_refuses_changed_target_or_other_settings(context, binding):
    before = _existing(context)
    plan = _plan(context, binding, before)
    installed = _insert(context, plan, before)
    with pytest.raises(HeinzelError, match="original MCP config"):
        plan.verify(before + b" ", installed, _selected(context, binding))
    with pytest.raises(HeinzelError, match="differs"):
        plan.verify(before, installed.replace(b"true", b"1"), _selected(context, binding))
    with pytest.raises(HeinzelError, match="differs"):
        plan.verify(before, installed.replace(b"bridge.py", b"other.py"), _selected(context, binding))
    with pytest.raises(HeinzelError, match="recorded bridge"):
        plan_manual_client_config(
            ClientConfigAction.UNINSTALL,
            context,
            binding,
            installed.replace(b"bridge.py", b"other.py"),
            _selected(context, binding),
        )


def test_native_selected_entry_must_be_complete_and_from_same_context(context, binding):
    plan = _plan(context, binding)
    installed = plan.install_snippet().encode()
    selected = _selected(context, binding)
    assert selected.entry is not None
    variants = [
        replace(selected, name="another"),
        replace(selected, context=replace(context, workspace=Path("/fixture/another-workspace"))),
        replace(selected, source=context.workspace / "project-config"),
        replace(selected, status=NativeConfigStatus.UNAVAILABLE),
        replace(selected, entry={**selected.entry, "env": {"ARBITRARY": "value"}}),
    ]
    for variant in variants:
        with pytest.raises(HeinzelError, match="different entry"):
            plan.verify(None, installed, variant)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"mcpServers":{},"mcpServers":{}}',
        b'{"mcpServers":{"entry":{},"entry":{}}}',
        b'{"mcpServers":[]}',
        b'{"other":NaN}',
        b'{"other":"\xff"}',
        b"[]",
    ],
)
def test_malformed_json_refuses_without_reporting_bytes(raw, binding):
    context = ClientConfigContext(McpClient.CURSOR, Path("/fixture/user"), Path("/fixture/workspace"))
    with pytest.raises(HeinzelError, match="invalid or has an ambiguous server table"):
        _plan(context, binding, raw)


@pytest.mark.parametrize(
    "raw",
    [b"mcp_servers = []\n", b"[mcp_servers]\n[mcp_servers]\n", b"[mcp_servers\n", b"x = 1\nx = 2\n"],
)
def test_malformed_codex_toml_refuses(raw, binding):
    context = ClientConfigContext(McpClient.CODEX, Path("/fixture/user"), Path("/fixture/workspace"))
    with pytest.raises(HeinzelError, match="invalid or has an ambiguous server table"):
        _plan(context, binding, raw)


@pytest.mark.parametrize("path", ["python", "/fixture/../python", "/fixture/${env:PYTHON}", "/fixture/bad\nname"])
def test_invalid_bridge_launcher_paths_refuse(context, binding, path):
    with pytest.raises(HeinzelError, match="bridge path is invalid"):
        _plan(context, replace(binding, interpreter=Path(path)))


def test_logs_only_safe_fields(context, binding, caplog):
    before = _existing(context).replace(b"https://example.test/mcp", b"fixture-confidential-config")
    with caplog.at_level(logging.INFO, logger="heinzel.mcp"):
        plan = _plan(context, binding, before)
        with pytest.raises(HeinzelError):
            plan.verify(before, b"invalid fixture-confidential-config", _selected(context, binding))
    records = [record_fields(record) for record in caplog.records]
    assert [(record["phase"], record.get("outcome")) for record in records] == [
        ("start", None),
        ("end", "ok"),
        ("start", None),
        ("end", "error"),
    ]
    assert "fixture-confidential-config" not in repr(records)
    assert str(context.path) not in repr(records)
    assert binding.name not in repr(records)


def test_uninstall_does_not_delete_the_config_file(context, binding):
    installed = _plan(context, binding).install_snippet().encode()
    removal = plan_manual_client_config(
        ClientConfigAction.UNINSTALL, context, binding, installed, _selected(context, binding)
    )
    with pytest.raises(HeinzelError, match="differs"):
        removal.verify(installed, None, _absent(context, binding))
    empty = b"" if context.client is McpClient.CODEX else b"{}"
    removal.verify(installed, empty, _absent(context, binding))


def test_server_name_is_stable_and_vendor_safe(context, binding):
    plan = _plan(context, binding)
    assert plan.binding.name == binding.name
    assert binding.name.startswith("heinzel-")
    assert len(binding.name) == len("heinzel-") + 32
    assert all(character in "0123456789abcdef" for character in binding.name.removeprefix("heinzel-"))
    other = replace(binding, registration="another-registration")
    assert _plan(context, other).digest != plan.digest
    with pytest.raises(HeinzelError, match="registration locator"):
        _plan(context, replace(binding, registration=""))
