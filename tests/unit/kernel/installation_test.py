import os
import subprocess
import sys

import pytest

from heinzel import HeinzelError, Progress
from heinzel.kernel import Installation, _installation, requirement_from_receipt

pytestmark = pytest.mark.unit

PYTHON = f"{sys.version_info.major}.{sys.version_info.minor}"
RECEIPT = """
[tool]
requirements = [
    { name = "heinzel", git = "https://github.com/org/heinzel?tag=v0.1.0" },
    { name = "heinzel-old", git = "https://github.com/org/heinzel-old" },
]
"""


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"name": "heinzel"}, "heinzel"),
        ({"name": "heinzel", "specifier": ">=0.1"}, "heinzel>=0.1"),
        ({"name": "heinzel", "extras": ["a", "b"]}, "heinzel[a,b]"),
        ({"name": "heinzel", "git": "https://github.com/org/heinzel"}, "heinzel @ git+https://github.com/org/heinzel"),
        (
            {"name": "heinzel", "git": "https://github.com/org/heinzel?rev=v1"},
            "heinzel @ git+https://github.com/org/heinzel@v1",
        ),
        (
            {"name": "heinzel", "git": "https://github.com/org/heinzel?tag=v1"},
            "heinzel @ git+https://github.com/org/heinzel@v1",
        ),
        (
            {"name": "heinzel", "git": "https://github.com/org/mono?branch=main&subdirectory=heinzel"},
            "heinzel @ git+https://github.com/org/mono@main#subdirectory=heinzel",
        ),
        ({"name": "heinzel", "directory": "/src/heinzel"}, "heinzel @ file:///src/heinzel"),
        ({"name": "heinzel", "url": "https://example.com/heinzel.whl"}, "heinzel @ https://example.com/heinzel.whl"),
    ],
)
def test_requirement_from_receipt(entry, expected):
    assert requirement_from_receipt(entry) == expected


def test_tool_environment(tmp_path):
    (tmp_path / "uv-receipt.toml").write_text(RECEIPT, encoding="utf-8")
    installation = Installation(Progress(), prefix=tmp_path, uv="uv")
    assert installation.is_tool
    assert installation.requirements() == [
        "heinzel @ git+https://github.com/org/heinzel@v0.1.0",
        "heinzel-old @ git+https://github.com/org/heinzel-old",
    ]
    assert installation.tool_install_command("heinzel", ["a", "b"]) == [
        "uv", "tool", "install", "--reinstall", "--python", PYTHON, "heinzel", "--with", "a", "--with", "b",
    ]  # fmt: skip
    assert installation.tool_install_command("heinzel", ["a"], refresh=True) == [
        "uv", "tool", "install", "--reinstall", "--refresh", "--python", PYTHON, "heinzel", "--with", "a",
    ]  # fmt: skip
    assert [pin.requirement for pin in installation.plugin_pins()] == [
        "heinzel-old @ git+https://github.com/org/heinzel-old"
    ]


def test_other_environments(tmp_path):
    installation = Installation(Progress(), prefix=tmp_path, uv="uv")
    assert not installation.is_tool
    assert installation.requirements() is None
    assert installation.pip_command("install", "x") == ["uv", "pip", "install", "--python", sys.executable, "x"]


def test_requires_uv(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(HeinzelError, match="uv is required"):
        _ = Installation(Progress(), prefix=tmp_path).uv


def test_run_reports_failures_with_output(tmp_path):
    def failing(command):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="uv says no")

    installation = Installation(Progress(), prefix=tmp_path, uv="uv", runner=failing)
    with pytest.raises(HeinzelError, match=r"Broke\.\nuv says no"):
        installation.run(["uv"], "Broke.", "Doing")


def test_hz_runs_a_fresh_interpreter(tmp_path, monkeypatch):
    # The real interpreter start is in tests/integration/installation_test.py; here only what is started and how a
    # failure is reported.
    commands = []

    def answering(command, **kwargs):
        commands.append(command)
        ok = command[-1] == "--version"
        return subprocess.CompletedProcess(command, 0 if ok else 2, "hz 9.9.9\n" if ok else "", "" if ok else "boom")

    monkeypatch.setattr(subprocess, "run", answering)
    installation = Installation(Progress(), prefix=tmp_path, uv="uv")
    assert installation.hz("--version") == "hz 9.9.9\n"
    with pytest.raises(HeinzelError, match="failed after the change:\nboom"):
        installation.hz("no-such-command")
    assert commands == [
        [sys.executable, "-m", "heinzel", "--version"],
        [sys.executable, "-m", "heinzel", "no-such-command"],
    ]


def test_commands_that_hang_time_out(tmp_path, monkeypatch):
    def hanging(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", hanging)
    result = _installation._run(["uv", "tool", "install", "x"])
    assert (result.returncode, result.stderr) == (124, f"uv timed out after {_installation.COMMAND_TIMEOUT}s")
    with pytest.raises(HeinzelError, match="did not finish within"):
        Installation(Progress(), prefix=tmp_path, uv="uv").hz("--version")


def test_uv_and_hz_run_without_credential_variables(tmp_path, monkeypatch):
    environments = []

    def recording(command, **kwargs):
        environments.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, "hz 9.9.9", "")

    monkeypatch.setenv("HZ_CREDENTIAL_EXAMPLE_API_TOKEN", "not-for-uv")
    monkeypatch.setattr(subprocess, "run", recording)
    _installation._run(["uv", "tool", "list"])
    Installation(Progress(), prefix=tmp_path, uv="uv").hz("--version")
    assert len(environments) == 2
    assert all("HZ_CREDENTIAL_EXAMPLE_API_TOKEN" not in env and "PATH" in env for env in environments)


def test_run_adds_variables_to_the_environment_when_asked(tmp_path, monkeypatch):
    environments = []

    def recording(command, **kwargs):
        environments.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", recording)
    Installation(Progress(), prefix=tmp_path, uv="uv").run(["uv"], "Broke.", "Doing", {"GIT_CONFIG_COUNT": "1"})
    Installation(Progress(), prefix=tmp_path, uv="uv").run(["uv"], "Broke.", "Doing")
    assert environments[0]["GIT_CONFIG_COUNT"] == "1"
    assert "PATH" in environments[0]
    assert environments[1].get("GIT_CONFIG_COUNT") == os.environ.get("GIT_CONFIG_COUNT")


def test_the_hz_probe_never_checks_for_updates(tmp_path, monkeypatch):
    environments = []

    def recording(command, **kwargs):
        environments.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, "hz 9.9.9", "")

    monkeypatch.setenv("HZ_UPDATE_CHECK", "true")
    monkeypatch.setattr(subprocess, "run", recording)
    Installation(Progress(), prefix=tmp_path, uv="uv").hz("--version")
    assert environments[0]["HZ_UPDATE_CHECK"] == "false"


REPO = "https://github.com/example-org/heinzel"


@pytest.mark.parametrize(
    ("entry", "kind", "ref"),
    [
        # What the README's `uv tool install git+…@vX.Y.Z` leaves in the receipt.
        ({"name": "heinzel", "git": f"{REPO}?rev=v0.2.0"}, "release", "v0.2.0"),
        ({"name": "heinzel", "git": f"{REPO}?tag=v0.2.0"}, "release", "v0.2.0"),
        ({"name": "heinzel", "git": f"{REPO}?rev=v0.5.0rc1"}, "release", "v0.5.0rc1"),
        ({"name": "heinzel", "git": f"{REPO}?rev=main"}, "ref", "main"),
        ({"name": "heinzel", "git": f"{REPO}?branch=v1.2.3"}, "ref", "v1.2.3"),  # a branch named like a release
        ({"name": "heinzel", "git": f"{REPO}?tag=stable"}, "ref", "stable"),
        ({"name": "heinzel", "git": f"{REPO}?rev=v1.2"}, "ref", "v1.2"),
        ({"name": "heinzel", "git": f"{REPO}?rev=1a2b3c4"}, "commit", "1a2b3c4"),
        ({"name": "heinzel", "git": f"{REPO}?rev={'a' * 40}"}, "commit", "a" * 40),
        ({"name": "heinzel", "git": f"{REPO}?tag=1a2b3c4"}, "ref", "1a2b3c4"),
        ({"name": "heinzel", "git": REPO}, "default", None),
        ({"name": "heinzel", "directory": "/src/heinzel"}, "local", "/src/heinzel"),
        ({"name": "heinzel", "editable": "/src/heinzel"}, "local", "/src/heinzel"),
        ({"name": "heinzel", "url": "https://example.com/heinzel.whl"}, "url", "https://example.com/heinzel.whl"),
        ({"name": "heinzel", "specifier": ">=0.1"}, "index", ">=0.1"),
        ({"name": "heinzel"}, "index", None),
    ],
)
def test_pin_from_receipt_says_what_the_core_is_pinned_to(entry, kind, ref):
    pin = _installation.pin_from_receipt(entry)
    assert (pin.kind, pin.ref) == (kind, ref)
    assert pin.requirement == requirement_from_receipt(entry)


def test_a_pin_moves_to_another_ref_keeping_extras_and_subdirectory():
    pin = _installation.pin_from_receipt(
        {"name": "heinzel", "extras": ["web"], "git": f"{REPO}.git?rev=v0.2.0&subdirectory=core"}
    )
    assert (pin.version, pin.git) == ("0.2.0", f"{REPO}.git")
    assert pin.source_at("v0.4.0") == f"git+{REPO}.git@v0.4.0#subdirectory=core"
    assert pin.at("v0.4.0") == f"heinzel[web] @ git+{REPO}.git@v0.4.0#subdirectory=core"


def test_only_a_release_tag_has_a_version_and_only_git_has_refs():
    assert _installation.pin_from_receipt({"name": "heinzel", "git": f"{REPO}?rev=main"}).version is None
    pin = _installation.pin_from_receipt({"name": "heinzel", "directory": "/src/heinzel"})
    with pytest.raises(HeinzelError, match="isn't installed from git"):
        pin.source_at("v1.0.0")


def test_core_pin_reads_the_first_requirement_of_the_receipt(tmp_path):
    assert Installation(Progress(), prefix=tmp_path, uv="uv").core_pin() is None
    (tmp_path / "uv-receipt.toml").write_text(RECEIPT, encoding="utf-8")
    pin = Installation(Progress(), prefix=tmp_path, uv="uv").core_pin()
    assert pin is not None
    assert (pin.kind, pin.ref, pin.git) == ("release", "v0.1.0", "https://github.com/org/heinzel")


def test_attempt_returns_how_the_command_ended(tmp_path):
    def failing(command):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="uv says no")

    result = Installation(Progress(), prefix=tmp_path, uv="uv", runner=failing).attempt(["uv"], "Doing")
    assert (result.returncode, result.stderr) == (1, "uv says no")


def _target_fixture(tmp_path):
    prefix = tmp_path / "tools" / "heinzel"
    scripts = prefix / "bin"
    scripts.mkdir(parents=True)
    (scripts / "hz").write_text("fixture", encoding="utf-8")
    bin_dir = tmp_path / "installed-bin"
    bin_dir.mkdir()
    (bin_dir / "hz").symlink_to(scripts / "hz")
    receipt = (
        '[tool]\nrequirements = [{ name = "heinzel" }]\nentrypoints = [{ name = "hz", install-path = '
        + repr(str(bin_dir / "hz"))
        + " }]\n"
    )
    (prefix / "uv-receipt.toml").write_text(receipt, encoding="utf-8")
    return prefix, bin_dir


def test_tool_install_binds_store_and_bin_to_running_receipt(tmp_path, monkeypatch):
    prefix, bin_dir = _target_fixture(tmp_path)
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "other-tools"))
    monkeypatch.setenv("UV_TOOL_BIN_DIR", str(tmp_path / "other-bin"))
    calls = []

    def spy(command, environment):
        calls.append((command, environment))
        return subprocess.CompletedProcess(command, 0, "", "")

    installation = Installation(Progress(), prefix=prefix, uv="fake-uv", runner=spy)
    command = installation.tool_install_command("heinzel", [])
    installation.attempt(command, "fixture", {"EXAMPLE": "kept", "UV_TOOL_DIR": "/wrong"})
    assert calls == [(command, {"EXAMPLE": "kept", "UV_TOOL_DIR": str(prefix.parent), "UV_TOOL_BIN_DIR": str(bin_dir)})]


@pytest.mark.parametrize("damage", ["missing", "wrong_target", "multiple", "relative", "wrong_prefix"])
def test_tool_install_refuses_unverified_target_without_running_uv(tmp_path, damage):
    prefix, bin_dir = _target_fixture(tmp_path)
    receipt = prefix / "uv-receipt.toml"
    if damage == "missing":
        receipt.write_text('[tool]\nrequirements = [{ name = "heinzel" }]\n', encoding="utf-8")
    elif damage == "wrong_target":
        (bin_dir / "hz").unlink()
        (bin_dir / "hz").symlink_to(tmp_path / "other")
    elif damage == "multiple":
        receipt.write_text(
            receipt.read_text(encoding="utf-8").replace(" }]\n", ' }, { name = "hz", install-path = "/wrong/hz" }]\n'),
            encoding="utf-8",
        )
    elif damage == "relative":
        receipt.write_text(
            receipt.read_text(encoding="utf-8").replace(str(bin_dir / "hz"), "relative/hz"), encoding="utf-8"
        )
    else:
        renamed = prefix.with_name("other")
        prefix.rename(renamed)
        prefix = renamed
    installation = Installation(Progress(), prefix=prefix, uv="fake-uv", runner=lambda *_args: pytest.fail("uv ran"))
    with pytest.raises(HeinzelError, match="Cannot verify the running uv tool"):
        installation.attempt(installation.tool_install_command("heinzel", []), "fixture")


def test_tool_target_reaches_guarded_subprocess_and_drops_credentials(tmp_path, monkeypatch):
    import json

    prefix, bin_dir = _target_fixture(tmp_path)
    spy = tmp_path / "uv"
    spy.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "print(json.dumps({key: os.environ.get(key) for key in "
        "('UV_TOOL_DIR', 'UV_TOOL_BIN_DIR', 'HZ_CREDENTIAL_EXAMPLE_TOKEN')}))\n"
        "sys.exit(77)\n",
        encoding="utf-8",
    )
    spy.chmod(0o700)
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "wrong-store"))
    monkeypatch.setenv("UV_TOOL_BIN_DIR", str(tmp_path / "wrong-bin"))
    monkeypatch.setenv("HZ_CREDENTIAL_EXAMPLE_TOKEN", "fictional-secret")
    installation = Installation(Progress(), prefix=prefix, uv=str(spy))
    result = installation.attempt(installation.tool_install_command("heinzel", []), "fake guarded uv")
    assert result.returncode == 77  # The fake refuses to perform any mutation.
    assert json.loads(result.stdout) == {
        "UV_TOOL_DIR": str(prefix.parent),
        "UV_TOOL_BIN_DIR": str(bin_dir),
        "HZ_CREDENTIAL_EXAMPLE_TOKEN": None,
    }


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("heinzel", "heinzel"),
        ("heinzel>=0.9", "heinzel>=0.9"),
        ("git+https://example.org/project.git@main", "heinzel @ git+https://example.org/project.git@main"),
        (
            "https://example.org/heinzel-0.9.6-py3-none-any.whl",
            "heinzel @ https://example.org/heinzel-0.9.6-py3-none-any.whl",
        ),
    ],
)
def test_tool_source_explicitly_names_heinzel_before_uv(tmp_path, source, expected):
    installation = Installation(Progress(), prefix=tmp_path, uv="fake-uv")
    assert installation.tool_install_command(source, [])[-1] == expected


def test_tool_source_accepts_existing_local_wheel_and_directory(tmp_path):
    installation = Installation(Progress(), prefix=tmp_path, uv="fake-uv")
    wheel = tmp_path / "heinzel-0.9.6-py3-none-any.whl"
    wheel.touch()
    assert installation.tool_install_command(str(wheel), [])[-1] == f"heinzel @ {wheel.as_uri()}"
    assert installation.tool_install_command(str(tmp_path), [])[-1] == f"heinzel @ {tmp_path.as_uri()}"


@pytest.mark.parametrize(
    "source",
    [
        "another-package",
        "another-package @ git+https://example.org/repo.git",
        "https://example.org/other_package-1.0-py3-none-any.whl",
        "heinzel @ https://example.org/other_package-1.0-py3-none-any.whl",
        "--help",
        "heinzel; python_version > '0'",
    ],
)
def test_tool_source_rejects_wrong_name_without_running_uv(tmp_path, source):
    installation = Installation(Progress(), prefix=tmp_path, uv="fake-uv", runner=lambda *_args: pytest.fail("uv ran"))
    with pytest.raises(HeinzelError, match="must install the heinzel distribution"):
        installation.tool_install_command(source, [])


def test_copied_entrypoint_and_missing_receipt_refuse_update(tmp_path):
    prefix, bin_dir = _target_fixture(tmp_path)
    shim = bin_dir / "hz"
    shim.unlink()
    shim.write_text("fixture", encoding="utf-8")
    installation = Installation(Progress(), prefix=prefix, uv="fake-uv", runner=lambda *_args: pytest.fail("uv ran"))
    with pytest.raises(HeinzelError, match="Cannot verify the running uv tool"):
        installation.attempt(installation.tool_install_command("heinzel", []), "fixture")
    (prefix / "uv-receipt.toml").unlink()
    with pytest.raises(HeinzelError, match="Cannot verify the running uv tool"):
        installation.attempt(installation.tool_install_command("heinzel", []), "restore fixture")


def test_scoped_target_refuses_replaced_bin_directory(tmp_path):
    prefix, bin_dir = _target_fixture(tmp_path)
    installation = Installation(Progress(), prefix=prefix, uv="fake-uv", runner=lambda *_args: pytest.fail("uv ran"))
    with installation.tool_transaction():
        bin_dir.rename(bin_dir.with_name("original"))
        bin_dir.mkdir()
        with pytest.raises(HeinzelError, match="verified uv tool target changed"):
            installation.attempt(installation.tool_install_command("heinzel", []), "fixture")


def test_failed_plugin_change_restores_tool_after_receipt_and_shim_disappear(tmp_path):
    prefix, bin_dir = _target_fixture(tmp_path)
    receipt = prefix / "uv-receipt.toml"
    original = receipt.read_text(encoding="utf-8")
    calls = []

    def fake_uv(command, environment):
        calls.append((command, environment))
        if len(calls) == 1:
            receipt.unlink()
            (bin_dir / "hz").unlink()
            return subprocess.CompletedProcess(command, 2, "", "foreign executable already exists")
        receipt.write_text(original, encoding="utf-8")
        (bin_dir / "hz").symlink_to(prefix / "bin" / "hz")
        return subprocess.CompletedProcess(command, 0, "", "")

    installation = Installation(Progress(), prefix=prefix, uv="fake-uv", runner=fake_uv)
    installation.hz = lambda *_args: "hz 0.9.6"  # type: ignore[method-assign]
    with pytest.raises(HeinzelError, match="previous tool was restored"):
        installation.run(installation.tool_install_command("heinzel", ["plugin"]), "Add failed", "Adding")
    assert len(calls) == 2
    assert calls[1][0] == installation.tool_install_command("heinzel", [])
    assert calls[0][1] == calls[1][1] == {"UV_TOOL_DIR": str(prefix.parent), "UV_TOOL_BIN_DIR": str(bin_dir)}


def test_opt_in_current_installed_receipt_target_read_only():
    from pathlib import Path

    prefix = os.environ.get("HZ_READONLY_UV_PREFIX")
    if not prefix:
        pytest.skip("Set HZ_READONLY_UV_PREFIX to inspect an installed tool receipt without mutations")
    installation = Installation(Progress(), prefix=Path(prefix), uv="unused")
    target = installation._tool_target()
    assert target["UV_TOOL_DIR"] == str(Path(prefix).resolve(strict=True).parent)
    assert (Path(target["UV_TOOL_BIN_DIR"]) / "hz").resolve(strict=True) == (
        Path(prefix).resolve(strict=True) / "bin" / "hz"
    ).resolve(strict=True)


def test_dangling_foreign_executable_refuses_before_runner(tmp_path):
    prefix, bin_dir = _target_fixture(tmp_path)
    dangling = bin_dir / "unrelated"
    dangling.symlink_to(tmp_path / "absent")
    installation = Installation(Progress(), prefix=prefix, uv="fake-uv", runner=lambda *_args: pytest.fail("uv ran"))
    with pytest.raises(HeinzelError, match="unresolved uv executable symlinks"):
        installation.attempt(installation.tool_install_command("heinzel", []), "fixture")
    with pytest.raises(HeinzelError, match="unresolved uv executable symlinks"), installation.tool_transaction():
        pytest.fail("transaction started")
    assert dangling.is_symlink()
    assert dangling.readlink() == tmp_path / "absent"


def test_new_dangling_link_during_transaction_refuses_next_runner(tmp_path):
    prefix, bin_dir = _target_fixture(tmp_path)
    installation = Installation(Progress(), prefix=prefix, uv="fake-uv", runner=lambda *_args: pytest.fail("uv ran"))
    with installation.tool_transaction():
        dangling = bin_dir / "foreign"
        dangling.symlink_to(tmp_path / "absent")
        with pytest.raises(HeinzelError, match="unresolved uv executable symlinks"):
            installation.attempt(installation.tool_install_command("heinzel", []), "fixture")
        assert dangling.is_symlink()


def test_valid_foreign_symlink_does_not_block_tool_target(tmp_path):
    prefix, bin_dir = _target_fixture(tmp_path)
    foreign_target = tmp_path / "foreign-target"
    foreign_target.write_text("fixture", encoding="utf-8")
    (bin_dir / "foreign").symlink_to(foreign_target)
    calls = []

    def fake_uv(command, environment):
        calls.append((command, environment))
        return subprocess.CompletedProcess(command, 0, "", "")

    installation = Installation(Progress(), prefix=prefix, uv="fake-uv", runner=fake_uv)
    installation.attempt(installation.tool_install_command("heinzel", []), "fixture")
    assert len(calls) == 1


def test_only_original_receipt_owned_link_can_be_dangling_for_scoped_recovery(tmp_path):
    prefix, bin_dir = _target_fixture(tmp_path)
    calls = []

    def fake_uv(command, environment):
        calls.append((command, environment))
        return subprocess.CompletedProcess(command, 0, "", "")

    installation = Installation(Progress(), prefix=prefix, uv="fake-uv", runner=fake_uv)
    command = installation.tool_install_command("heinzel", [])
    with installation.tool_transaction():
        (prefix / "bin" / "hz").unlink()
        with pytest.raises(HeinzelError, match="unresolved uv executable symlinks"):
            installation.attempt(command, "ordinary retry")
        installation.attempt(command, "restore", recovery=True)
        foreign = bin_dir / "foreign"
        foreign.symlink_to(tmp_path / "missing")
        with pytest.raises(HeinzelError, match="unresolved uv executable symlinks"):
            installation.attempt(command, "restore with foreign link", recovery=True)
        foreign.unlink()
        shim = bin_dir / "hz"
        old = bin_dir / "old-shim"
        shim.rename(old)
        shim.symlink_to(prefix / "bin" / "hz")
        old.unlink()
        with pytest.raises(HeinzelError, match="unresolved uv executable symlinks"):
            installation.attempt(command, "restore with replaced link", recovery=True)
    assert len(calls) == 1


def test_hz_probe_reports_missing_interpreter_as_heinzel_error(tmp_path, monkeypatch):
    def missing(*_args, **_kwargs):
        msg = "fixture interpreter removed"
        raise FileNotFoundError(msg)

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(HeinzelError, match="could not start after the change"):
        Installation(Progress(), prefix=tmp_path, uv="fake-uv").hz("--version")
