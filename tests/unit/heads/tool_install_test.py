"""Initial installations and managed refreshes retain explicit executable ownership."""

import hashlib
import json
import os
import subprocess
import sys

import pytest

from heinzel import HeinzelError
from heinzel.heads import _tool_install as tool

pytestmark = pytest.mark.unit
COMMAND = ["/trusted/uv", "tool", "install", "heinzel"]
WHEEL_HASH = hashlib.sha256(b"verified fixture").hexdigest()
EXPECTED = {"heinzel": ("1.0", WHEEL_HASH)}


@pytest.fixture
def target(tmp_path):
    target = tool.ToolTarget(tmp_path / "tools", tmp_path / "bin")
    target.store.mkdir()
    target.binary.mkdir()
    return target


def write_tool(target):
    (target.prefix / "bin").mkdir(parents=True)
    interpreter = target.store / "base-python"
    assert not interpreter.exists()
    assert not interpreter.is_symlink()
    interpreter.write_text("base interpreter")
    assert interpreter.resolve().is_relative_to(target.store.resolve())
    assert not interpreter.samefile(sys.executable)
    assert not interpreter.samefile(sys._base_executable)
    interpreter.chmod(0o755)
    (target.prefix / "bin/python").symlink_to(interpreter)
    entries = []
    for name in tool.ENTRYPOINTS:
        executable = target.prefix / "bin" / name
        executable.write_text("installed fixture")
        (target.binary / name).symlink_to(executable)
        entries.append(f"{{name={json.dumps(name)}, install-path={json.dumps(str(target.binary / name))}}}")
    metadata = target.prefix / "lib/python3.12/site-packages/heinzel-1.0.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text("Name: heinzel\nVersion: 1.0\n")
    (metadata / "direct_url.json").write_text(
        json.dumps({"url": wheel(target).as_uri(), "archive_info": {"hashes": {"sha256": WHEEL_HASH}}})
    )
    (target.prefix / "uv-receipt.toml").write_text(
        '[tool]\nrequirements=[{name="heinzel"}]\nentrypoints=[' + ",".join(entries) + "]\n"
    )


def wheel(target):
    path = target.store / "heinzel-1.0-py3-none-any.whl"
    if not path.exists():
        assert not path.is_symlink()
        path.write_bytes(b"verified fixture")
    return path


def run_tool(command, target, expected):
    path = wheel(target)
    installation = tool.target_installation(target, command[0])
    return tool.run_tool(command, target, expected, {"heinzel": path}, installation, [])


def test_fresh_install_binds_both_targets_and_probes_the_managed_app(target, monkeypatch):
    calls = []
    monkeypatch.setenv("HZ_CREDENTIAL_EXAMPLE_SECRET", "fixture-private-value")

    def run(command, **kwargs):
        assert command == COMMAND
        assert kwargs["env"]["UV_TOOL_DIR"] == str(target.store)
        assert kwargs["env"]["UV_TOOL_BIN_DIR"] == str(target.binary)
        assert "HZ_CREDENTIAL_EXAMPLE_SECRET" not in kwargs["env"]
        assert kwargs["stdout"] is tool.sys.stderr
        assert kwargs["timeout"] == 600
        write_tool(target)
        receipt = target.prefix / "uv-receipt.toml"
        receipt.write_text(receipt.read_text() + f'overrides=[{{name="heinzel", path={str(wheel(target))!r}}}]\n')
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(tool.subprocess, "run", run)
    monkeypatch.setattr(
        tool._TargetInstallation, "hz", lambda self, *args: calls.append((self.prefix, args)) or "hz 1.0"
    )
    run_tool(COMMAND, target, EXPECTED)
    assert calls == [(target.prefix, ("--version",))]


def test_the_heads_command_preflight_does_not_claim_other_tools_names(target):
    (target.binary / "unrelated").symlink_to(target.store / "missing-other-tool")
    assert tool._identities(target)
    assert (target.binary / "unrelated").is_symlink()


@pytest.mark.parametrize("kind", ["file", "foreign-link", "dangling-link"])
def test_initial_command_collisions_are_refused_without_invoking_uv(target, monkeypatch, kind):
    command = target.binary / "heinzel"
    foreign = target.store / "foreign"
    foreign.write_text("keep me")
    if kind == "file":
        command.write_text("keep command")
    else:
        command.symlink_to(foreign if kind == "foreign-link" else target.store / "missing")
    before = command.lstat()
    monkeypatch.setattr(tool.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("Must refuse before uv"))
    with pytest.raises(HeinzelError, match=r"another installation|unresolved"):
        run_tool(COMMAND, target, EXPECTED)
    assert command.lstat().st_ino == before.st_ino
    assert foreign.read_text() == "keep me"
    assert not target.prefix.exists()


@pytest.mark.parametrize("failure", ["uv-error", "timeout", "probe-error"])
def test_a_failed_fresh_install_preserves_ambiguous_state_for_explicit_recovery(target, monkeypatch, failure):
    unrelated = target.binary / "unrelated"
    unrelated.write_text("preserve")

    def run(command, **_kwargs):
        write_tool(target)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 600)
        if failure == "uv-error":
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    def probe(_self, *_args):
        msg = "managed probe failed"
        raise HeinzelError(msg)

    monkeypatch.setattr(tool.subprocess, "run", run)
    monkeypatch.setattr(tool._TargetInstallation, "hz", probe)
    with pytest.raises(HeinzelError, match="partial state was kept"):
        run_tool(COMMAND, target, EXPECTED)
    assert target.prefix.is_dir()
    assert all(os.path.lexists(target.binary / name) for name in tool.ENTRYPOINTS)
    assert unrelated.read_text() == "preserve"


def test_failure_never_deletes_an_unverifiable_partial_directory(target, monkeypatch):
    def run(command, **_kwargs):
        target.prefix.mkdir()
        (target.prefix / "foreign").write_text("preserve")
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(tool.subprocess, "run", run)
    with pytest.raises(HeinzelError, match="uv tool uninstall"):
        run_tool(COMMAND, target, EXPECTED)
    assert (target.prefix / "foreign").read_text() == "preserve"


def test_a_concurrent_valid_looking_tool_is_not_deleted_after_initial_failure(target, monkeypatch):
    def run(command, **_kwargs):
        write_tool(target)
        (target.prefix / "concurrent-owner").write_text("not this operation")
        (target.binary / "concurrent-command").write_text("keep")
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(tool.subprocess, "run", run)
    with pytest.raises(HeinzelError, match="partial state was kept"):
        run_tool(COMMAND, target, EXPECTED)
    assert (target.prefix / "concurrent-owner").read_text() == "not this operation"
    assert (target.binary / "concurrent-command").read_text() == "keep"
    assert (target.prefix / "uv-receipt.toml").is_file()


@pytest.mark.parametrize("identity", ["old-version", "wrong-hash", "index", "git", "editable"])
def test_fresh_uv_noop_cannot_claim_another_artifact_was_installed(target, monkeypatch, identity):
    def run(command, **_kwargs):
        assert "--reinstall" not in command
        write_tool(target)
        (target.prefix / "concurrent-owner").write_text("keep")
        metadata = target.prefix / "lib/python3.12/site-packages/heinzel-1.0.dist-info"
        direct = metadata / "direct_url.json"
        if identity == "old-version":
            (metadata / "METADATA").write_text("Name: heinzel\nVersion: 0.9\n")
        elif identity == "index":
            direct.unlink()
        else:
            document = json.loads(direct.read_text())
            if identity == "wrong-hash":
                document["archive_info"]["hashes"]["sha256"] = "b" * 64
            elif identity == "git":
                document["vcs_info"] = {"vcs": "git"}
            else:
                document["dir_info"] = {"editable": True}
            direct.write_text(json.dumps(document))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(tool.subprocess, "run", run)
    monkeypatch.setattr(tool._TargetInstallation, "hz", lambda *_args: pytest.fail("Never execute an unverified app"))
    with pytest.raises(HeinzelError, match="partial state was kept"):
        run_tool(COMMAND, target, EXPECTED)
    assert (target.prefix / "concurrent-owner").read_text() == "keep"


def test_refresh_delegates_to_the_guarded_kernel_and_keeps_its_failure(target, monkeypatch):
    write_tool(target)
    calls = []

    def run(self, command, failure, activity, environment):
        calls.append((self.prefix, command, environment))
        msg = "guarded recovery result"
        raise HeinzelError(msg)

    monkeypatch.setattr(tool._TargetInstallation, "run", run)
    with pytest.raises(HeinzelError, match="guarded recovery result"):
        run_tool(COMMAND, target, EXPECTED)
    assert calls == [
        (
            target.prefix,
            [*COMMAND[:3], "--reinstall", *COMMAND[3:], "--python", str(target.store / "base-python")],
            target.environment,
        )
    ]
    assert (target.prefix / "uv-receipt.toml").is_file()


def test_refresh_refuses_to_downgrade_the_installed_core_before_uv(target, monkeypatch):
    write_tool(target)
    monkeypatch.setattr(tool._TargetInstallation, "run", lambda *_args: pytest.fail("Must refuse before uv"))
    with pytest.raises(HeinzelError, match="would downgrade"):
        run_tool(COMMAND, target, {"heinzel": ("0.9", WHEEL_HASH)})
    assert (target.prefix / "uv-receipt.toml").is_file()


def test_probe_uses_the_managed_python_instead_of_the_bootstrap_sdk(target, monkeypatch):
    commands = []
    monkeypatch.setenv("HZ_CREDENTIAL_EXAMPLE_SECRET", "fixture-private-value")
    monkeypatch.setenv("HZ_UPDATE_CHECK", "true")

    def run(command, **kwargs):
        commands.append(command)
        assert "HZ_CREDENTIAL_EXAMPLE_SECRET" not in kwargs["env"]
        assert kwargs["env"]["HZ_UPDATE_CHECK"] == "false"
        return subprocess.CompletedProcess(command, 0, "hz 1.0")

    monkeypatch.setattr(tool.subprocess, "run", run)
    installed = tool._TargetInstallation(target.prefix, "uv")
    assert installed.hz("--version") == "hz 1.0"
    assert commands == [[str(target.prefix / "bin/python"), "-I", "-m", "heinzel", "--version"]]


def test_recovery_preserves_the_existing_base_interpreter(target):
    write_tool(target)
    installed = tool._TargetInstallation(target.prefix, "uv")
    command = installed.tool_install_command("heinzel==1.0", [])
    assert command[command.index("--python") + 1] == str(target.store / "base-python")


def test_uv_local_wheel_source_preserves_the_explicitly_verified_url_hash(target):
    write_tool(target)
    direct = target.prefix / "lib/python3.12/site-packages/heinzel-1.0.dist-info/direct_url.json"
    direct.write_text(json.dumps({"url": wheel(target).as_uri() + "#sha256=" + WHEEL_HASH, "archive_info": {}}))
    tool.verify_artifacts(target, EXPECTED)


def test_a_receipt_cannot_redirect_the_selected_binary_directory(target, monkeypatch):
    write_tool(target)
    other = target.store / "other-bin"
    other.mkdir()
    for name in tool.ENTRYPOINTS:
        (other / name).symlink_to(target.prefix / "bin" / name)
    receipt = target.prefix / "uv-receipt.toml"
    receipt.write_text(receipt.read_text().replace(str(target.binary), str(other)))
    monkeypatch.setattr(tool._TargetInstallation, "run", lambda *_args: pytest.fail("Must refuse before uv"))
    with pytest.raises(HeinzelError, match="does not match"):
        run_tool(COMMAND, target, EXPECTED)


def test_an_existing_receipt_parent_alias_resolves_to_the_same_verified_directory(target):
    write_tool(target)
    alias = target.store / "binary-alias"
    alias.symlink_to(target.binary, target_is_directory=True)
    receipt = target.prefix / "uv-receipt.toml"
    receipt.write_text(receipt.read_text().replace(str(target.binary), str(alias)))
    tool._TargetInstallation(target.prefix, "uv").verify_tool_destination(target.store, target.binary)


def test_successful_uv_with_mismatched_refresh_metadata_retains_state_with_scoped_guidance(target, monkeypatch):
    write_tool(target)
    (target.prefix / "concurrent-owner").write_text("retain")
    monkeypatch.setattr(tool._TargetInstallation, "run", lambda *_args: None)
    wrong = {"heinzel": ("2.0", WHEEL_HASH)}
    with pytest.raises(HeinzelError, match="refresh could not be verified") as error:
        run_tool(COMMAND, target, wrong)
    assert "UV_TOOL_DIR=" in str(error.value)
    assert "UV_TOOL_BIN_DIR=" in str(error.value)
    assert (target.prefix / "concurrent-owner").read_text() == "retain"


def test_force_is_never_accepted_by_the_standard_installer(target, monkeypatch):
    monkeypatch.setattr(tool.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("Must refuse before uv"))
    with pytest.raises(HeinzelError, match="cannot overwrite"):
        run_tool([*COMMAND, "--force"], target, EXPECTED)


def test_resolves_both_uv_destinations_before_installing(tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_EXAMPLE_SECRET", "fixture-private-value")

    def run(command, **kwargs):
        assert "HZ_CREDENTIAL_EXAMPLE_SECRET" not in kwargs["env"]
        path = tmp_path / ("bin" if command[-1] == "--bin" else "tools")
        return subprocess.CompletedProcess(command, 0, str(path))

    monkeypatch.setattr(tool.subprocess, "run", run)
    target = tool.resolve_target("uv")
    assert target == tool.ToolTarget((tmp_path / "tools").resolve(), (tmp_path / "bin").resolve())
    assert target.prefix.name == "heinzel"


def test_symlinked_destination_is_refused(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(
        tool.subprocess, "run", lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, str(link))
    )
    with pytest.raises(HeinzelError, match="Cannot verify"):
        tool.resolve_target("uv")
