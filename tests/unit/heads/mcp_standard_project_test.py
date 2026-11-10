"""Ordinary project installation is explicit, creation-only, and never claims readiness."""

import json
import os
import sys
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from heinzel.heads.cli import build

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("client", "relative"),
    [("claude", ".mcp.json"), ("codex", ".codex/config.toml"), ("cursor", ".cursor/mcp.json")],
)
def test_explicit_install_creates_only_selected_project_file(make_kernel, tmp_path, monkeypatch, client, relative):
    home = tmp_path / "fictional-home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    destination = tmp_path / relative
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
    assert list(home.iterdir()) == []
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


def test_native_identity_mismatch_never_creates_configuration(make_kernel, tmp_path):
    info = tmp_path.stat()
    runner = CliRunner()
    app = build(make_kernel(builtins=True))
    args = ["system", "serve", "mcp-install-project", "--client", "claude", "--project", str(tmp_path)]
    mismatch = runner.invoke(
        app, [*args, "--expected-device", str(info.st_dev), "--expected-inode", str(info.st_ino + 1)]
    )
    assert mismatch.exit_code != 0
    assert not (tmp_path / ".mcp.json").exists()
    incomplete = runner.invoke(app, [*args, "--expected-device", str(info.st_dev)])
    assert incomplete.exit_code != 0
    assert not (tmp_path / ".mcp.json").exists()
    matched = runner.invoke(app, [*args, "--expected-device", str(info.st_dev), "--expected-inode", str(info.st_ino)])
    assert matched.exit_code == 0, matched.output


@pytest.mark.parametrize("client", ["claude", "codex", "cursor"])
@pytest.mark.parametrize("use_alias", [False, True])
def test_home_and_parent_symlink_alias_are_never_project_targets(make_kernel, tmp_path, monkeypatch, client, use_alias):
    home = tmp_path / "fictional-home"
    home.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    selected = alias / home.name if use_alias else home
    result = CliRunner().invoke(
        build(make_kernel(builtins=True)),
        ["system", "serve", "mcp-install-project", "--client", client, "--project", str(selected)],
    )
    assert result.exit_code == 1
    assert "other than your home" in result.output
    assert list(home.iterdir()) == []


def test_install_refuses_when_home_identity_cannot_be_checked(make_kernel, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "missing-home"))
    project = tmp_path / "project"
    project.mkdir()
    result = CliRunner().invoke(
        build(make_kernel(builtins=True)),
        ["system", "serve", "mcp-install-project", "--client", "claude", "--project", str(project)],
    )
    assert result.exit_code == 1
    assert list(project.iterdir()) == []


@pytest.mark.parametrize("existing", [False, True])
def test_codex_home_override_never_receives_project_configuration(make_kernel, tmp_path, monkeypatch, existing):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    global_dir = project / ".codex"
    if existing:
        global_dir.mkdir()
        override = tmp_path / "global-alias"
        override.symlink_to(global_dir, target_is_directory=True)
    else:
        override = alias / project.name / ".codex"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("CODEX_HOME", str(override))
    result = CliRunner().invoke(
        build(make_kernel(builtins=True)),
        ["system", "serve", "mcp-install-project", "--client", "codex", "--project", str(project)],
    )
    assert result.exit_code == 1
    assert "global Codex configuration" in result.output
    assert not (global_dir / "config.toml").exists()
    assert global_dir.exists() is existing  # Never create even the global directory on refusal.


@pytest.mark.parametrize("existing", [False, True])
def test_separate_codex_home_override_still_allows_project_install(make_kernel, tmp_path, monkeypatch, existing):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    codex_home = tmp_path / "separate-codex-home"
    if existing:
        codex_home.mkdir()
        (codex_home / "unrelated").write_text("untouched")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    result = CliRunner().invoke(
        build(make_kernel(builtins=True)),
        ["system", "serve", "mcp-install-project", "--client", "codex", "--project", str(project)],
    )
    assert result.exit_code == 0, result.output
    assert (project / ".codex/config.toml").is_file()
    if existing:
        assert [path.name for path in codex_home.iterdir()] == ["unrelated"]
        assert (codex_home / "unrelated").read_text() == "untouched"
    else:
        assert not codex_home.exists()


def test_missing_codex_home_parent_does_not_block_unrelated_project(make_kernel, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "missing" / ".codex"))
    result = CliRunner().invoke(
        build(make_kernel(builtins=True)),
        ["system", "serve", "mcp-install-project", "--client", "codex", "--project", str(project)],
    )
    assert result.exit_code == 0, result.output
    assert (project / ".codex/config.toml").is_file()
    assert not (tmp_path / "missing").exists()


def test_opened_codex_directory_matching_global_identity_is_refused(tmp_path):
    from heinzel import HeinzelError
    from heinzel.heads.mcp._standard_project import _reject_global_codex_dir

    global_dir = tmp_path / "codex-global"
    global_dir.mkdir()
    descriptor = os.open(global_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with pytest.raises(HeinzelError, match="global Codex configuration"):
            _reject_global_codex_dir(descriptor, global_dir)
        assert list(global_dir.iterdir()) == []
    finally:
        os.close(descriptor)
