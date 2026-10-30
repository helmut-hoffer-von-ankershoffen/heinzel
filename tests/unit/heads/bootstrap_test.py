"""One bare command installs a complete app once and opens its managed foreground workspace."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from heinzel.heads import bootstrap, install

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("already_installed", [False, True])
def test_first_use_installs_defaults_and_later_use_opens_without_installing(monkeypatch, tmp_path, already_installed):
    executable = tmp_path / "tools/heinzel/bin/hz"
    sequence = iter([executable] if already_installed else [None, executable])
    installed, launched = [], []
    monkeypatch.setattr(bootstrap.sys, "argv", ["heinzel"])
    monkeypatch.setattr(bootstrap, "installed_app", lambda: next(sequence))
    monkeypatch.setattr(install, "main", lambda args: installed.append(args) or 0)
    monkeypatch.setattr(bootstrap.os, "execve", lambda *args: launched.append(args))
    bootstrap.main()
    assert installed == ([] if already_installed else [[]])
    [program, args, environment] = launched[0]
    assert program == str(executable)
    assert args == [str(executable), "ui"]
    assert environment["PATH"].split(bootstrap.os.pathsep)[0] == str(executable.parent)


def test_explicit_arguments_keep_existing_cli_behavior(monkeypatch):
    from heinzel.heads import cli

    called = []
    monkeypatch.setattr(bootstrap.sys, "argv", ["heinzel", "--help"])
    monkeypatch.setattr(cli, "main", lambda: called.append(True))
    monkeypatch.setattr(bootstrap, "installed_app", lambda: pytest.fail("Explicit CLI arguments must not install"))
    bootstrap.main()
    assert called == [True]


@pytest.mark.parametrize("arguments", [["ui", "--no-browser"], ["join", "https://github.com/example/team"]])
def test_runtime_arguments_never_open_the_temporary_plugin_free_sdk(monkeypatch, tmp_path, arguments):
    executable = tmp_path / "managed/bin/hz"
    launched = []
    monkeypatch.setattr(bootstrap.sys, "argv", ["heinzel", *arguments])
    monkeypatch.setattr(bootstrap, "installed_app", lambda: executable)
    monkeypatch.setattr(bootstrap.os, "execve", lambda *args: launched.append(args))
    bootstrap.main()
    assert launched[0][1] == [str(executable), *arguments]


def test_a_failed_install_never_launches_a_partial_app(monkeypatch, capsys):
    monkeypatch.setattr(bootstrap.sys, "argv", ["heinzel"])
    monkeypatch.setattr(bootstrap, "installed_app", lambda: None)
    monkeypatch.setattr(install, "main", lambda _args: 1)
    monkeypatch.setattr(bootstrap.os, "execve", lambda *_args: pytest.fail("Must not open a failed install"))
    with pytest.raises(SystemExit) as result:
        bootstrap.main()
    assert result.value.code == 1
    assert "Installing Heinzelmännchen and its default plugins" in capsys.readouterr().err


@pytest.mark.parametrize("complete", [False, True])
def test_a_plain_sdk_is_not_mistaken_for_a_complete_standard_app(tmp_path, monkeypatch, complete):
    directory = tmp_path / "tools/heinzel/bin"
    directory.mkdir(parents=True)
    for name in ("hz", "python"):
        (directory / name).touch()
    data = tmp_path / "data"
    data.mkdir()
    names = ["heinzel", "heinzel-harbor"]
    profile = {
        "format": 1,
        "sdk": 1,
        "core_version": "1.0.0",
        "artifacts": [
            {
                "name": name,
                "version": "1.0.0",
                "filename": f"{name.replace('-', '_')}-1.0.0-py3-none-any.whl",
                "sha256": "a" * 64,
                "url": f"https://example.com/{name}.whl",
            }
            for name in names
        ],
    }
    (data / "standard-app.json").write_text(json.dumps(profile))
    monkeypatch.setattr(
        bootstrap.Config, "load", lambda: SimpleNamespace(settings=lambda *_: SimpleNamespace(data_dir=data))
    )
    monkeypatch.setattr(install, "uv_executable", lambda: "/trusted/uv")
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        if command[:3] == ["/trusted/uv", "tool", "dir"]:
            return subprocess.CompletedProcess(command, 0, str(tmp_path / "tools"), "")
        assert json.loads(command[-1]) == names
        return subprocess.CompletedProcess(command, 0 if complete else 1)

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    assert bootstrap.installed_app() == (directory / "hz" if complete else None)
    assert len(commands) == 2


def test_missing_tool_directory_is_first_use(monkeypatch):
    monkeypatch.setattr(install, "uv_executable", lambda: "/trusted/uv")
    monkeypatch.setattr(
        bootstrap.subprocess, "run", lambda command, **_: subprocess.CompletedProcess(command, 1, "", "")
    )
    assert bootstrap.installed_app() is None


def test_first_use_json_stdout_contains_only_the_managed_command_result(monkeypatch, tmp_path, capsys):
    executable = tmp_path / "tools/heinzel/bin/hz"
    sequence = iter([None, executable])
    monkeypatch.setattr(bootstrap, "installed_app", lambda: next(sequence))
    monkeypatch.setattr(bootstrap.sys, "argv", ["heinzel", "system", "info", "--json"])
    profile = install.AppProfile(
        format=1,
        sdk=1,
        core_version="1.0.0",
        artifacts=[
            install.Artifact(
                name="heinzel",
                version="1.0.0",
                filename="heinzel-1.0.0-py3-none-any.whl",
                sha256="a" * 64,
                url="https://example.com/heinzel.whl",
            )
        ],
    )
    monkeypatch.setattr(install, "latest_profile", lambda: profile)
    monkeypatch.setattr(install, "uv_executable", lambda: "/trusted/uv")
    monkeypatch.setattr(install, "existing_requirements", list)

    def prepare(_profile, directory, _excluded):
        directory.mkdir(parents=True)
        wheel = directory / profile.artifacts[0].filename
        wheel.write_bytes(b"unit fixture")
        return [wheel]

    monkeypatch.setattr(install, "prepare", prepare)

    def run(command, **kwargs):
        assert kwargs["stdout"] is bootstrap.sys.stderr
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(install.subprocess, "run", run)
    monkeypatch.setattr(bootstrap.os, "execve", lambda *_args: bootstrap.sys.stdout.write('{"ok":true}\n'))
    bootstrap.main()
    output = capsys.readouterr()
    assert json.loads(output.out) == {"ok": True}
    assert "Installing Heinzelmännchen" in output.err
    assert "heinzel 1.0.0" in output.err
