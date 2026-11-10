"""Ordinary project installation is explicit, creation-only, and never claims readiness."""

import json
import sys
import tomllib

import pytest
from typer.testing import CliRunner

from heinzel.heads.cli import build

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("client", "relative"),
    [("claude", ".mcp.json"), ("codex", ".codex/config.toml"), ("cursor", ".cursor/mcp.json")],
)
def test_explicit_install_creates_only_selected_project_file(make_kernel, tmp_path, client, relative):
    destination = tmp_path / relative
    destination.parent.mkdir(exist_ok=True)
    unrelated = tmp_path / "unrelated.json"
    unrelated.write_text('{"keep": true}')
    result = CliRunner().invoke(
        build(make_kernel(builtins=True)),
        ["system", "serve", "mcp-install-project", "--client", client, "--project", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["mcp_readiness"] == "unverified"
    contents = destination.read_bytes()
    config = tomllib.loads(contents.decode()) if client == "codex" else json.loads(contents)
    entry = config["mcp_servers" if client == "codex" else "mcpServers"]["heinzel"]
    assert entry == {"command": sys.executable, "args": ["-m", "heinzel", "system", "serve", "mcp"]}
    assert destination.stat().st_mode & 0o777 == 0o600
    assert unrelated.read_text() == '{"keep": true}'
    again = CliRunner().invoke(
        build(make_kernel(builtins=True)),
        ["system", "serve", "mcp-install-project", "--client", client, "--project", str(tmp_path)],
    )
    assert again.exit_code != 0
    assert destination.read_bytes() == contents


def test_existing_project_configuration_is_not_replaced(make_kernel, tmp_path):
    original = b'{"mcpServers":{"other":{"command":"existing"}}}'
    path = tmp_path / ".mcp.json"
    path.write_bytes(original)
    result = CliRunner().invoke(
        build(make_kernel(builtins=True)),
        ["system", "serve", "mcp-install-project", "--client", "claude", "--project", str(tmp_path)],
    )
    assert result.exit_code != 0
    assert path.read_bytes() == original


def test_symlinked_config_directory_is_refused(make_kernel, tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    (tmp_path / ".cursor").symlink_to(target, target_is_directory=True)
    result = CliRunner().invoke(
        build(make_kernel(builtins=True)),
        ["system", "serve", "mcp-install-project", "--client", "cursor", "--project", str(tmp_path)],
    )
    assert result.exit_code != 0
    assert list(target.iterdir()) == []


def test_symlinked_config_file_is_not_followed(make_kernel, tmp_path):
    target = tmp_path / "existing"
    target.write_text("untouched")
    (tmp_path / ".mcp.json").symlink_to(target)
    result = CliRunner().invoke(
        build(make_kernel(builtins=True)),
        ["system", "serve", "mcp-install-project", "--client", "claude", "--project", str(tmp_path)],
    )
    assert result.exit_code != 0
    assert target.read_text() == "untouched"
