"""The optional guided installer needs no Python and delegates to the same managed app launcher."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
INSTALLER = Path(__file__).resolve().parents[2] / "docs/install.sh"


def script(path, text):
    path.write_text("#!/bin/sh\nset -eu\n" + text)
    path.chmod(0o755)


@pytest.fixture
def machine(tmp_path):
    commands = tmp_path / "commands"
    commands.mkdir()
    for name in ("sh", "dirname", "mktemp", "rm", "rmdir", "mkdir", "cp", "chmod"):
        (commands / name).symlink_to(shutil.which(name))
    script(commands / "uname", 'printf "%s\\n" "${TEST_PLATFORM:-Darwin}"\n')
    binary = tmp_path / "fake-uv"
    script(
        binary, 'printf "%s\\n" "$@" > "$TRACE"\nif [ -n "${JSON_OUTPUT:-}" ]; then printf "%s\\n" "$JSON_OUTPUT"; fi\n'
    )
    upstream = tmp_path / "upstream.sh"
    upstream.write_text(
        'printf "upstream uv installed\\n"\nmkdir -p "$UV_INSTALL_DIR"\n'
        'cp "$UV_FAKE_BINARY" "$UV_INSTALL_DIR/uv"\nchmod +x "$UV_INSTALL_DIR/uv"\n'
    )
    env = {
        **os.environ,
        "PATH": str(commands),
        "UV_INSTALL_DIR": str(tmp_path / "uv bin"),
        "UV_FAKE_BINARY": str(binary),
        "UV_FAKE_INSTALLER": str(upstream),
        "TRACE": str(tmp_path / "trace"),
        "DOWNLOADS": str(tmp_path / "downloads"),
        "TMPDIR": str(tmp_path),
    }
    return tmp_path, commands, binary, env


def invoke(machine, *args):
    return subprocess.run(
        ["/bin/sh", "-s", "--", *args],
        input=INSTALLER.read_text(),
        env=machine[3],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def downloader(machine, name):
    script(
        machine[1] / name,
        """
printf '%s\\n' "$2" >> "$DOWNLOADS"
if [ "${DOWNLOAD_FAIL:-0}" = 1 ]; then exit 22; fi
cp "$UV_FAKE_INSTALLER" "$4"
""",
    )


def test_existing_uv_needs_no_download_and_preserves_command_arguments(machine):
    root, commands, binary, _ = machine
    (commands / "uv").symlink_to(binary)
    result = invoke(machine, "system", "info", "--json")
    assert result.returncode == 0, result.stderr
    assert (root / "trace").read_text().splitlines() == ["tool", "run", "heinzel@latest", "system", "info", "--json"]
    assert not (root / "downloads").exists()


def test_first_use_keeps_upstream_progress_out_of_runtime_json_stdout(machine):
    _, _, _, env = machine
    downloader(machine, "curl")
    env["JSON_OUTPUT"] = '{"ok":true}'
    result = invoke(machine, "system", "info", "--json")
    assert result.returncode == 0, result.stderr
    assert result.stdout == '{"ok":true}\n'
    assert "upstream uv installed" in result.stderr


@pytest.mark.parametrize("platform", ["Darwin", "Linux"])
@pytest.mark.parametrize("client", ["curl", "wget"])
def test_missing_uv_uses_the_official_installer_and_runs_without_a_shell_restart(machine, platform, client):
    root, _, _, env = machine
    env["TEST_PLATFORM"] = platform
    downloader(machine, client)
    result = invoke(machine)
    assert result.returncode == 0, result.stderr
    assert (root / "downloads").read_text().splitlines() == ["https://astral.sh/uv/install.sh"]
    assert (root / "trace").read_text().splitlines() == ["tool", "run", "heinzel@latest"]
    assert not list(root.glob("heinzel-install.*"))


def test_failed_download_never_executes_or_reports_a_completed_install(machine):
    root, _, _, env = machine
    downloader(machine, "curl")
    env["DOWNLOAD_FAIL"] = "1"
    result = invoke(machine)
    assert result.returncode == 22
    assert not (root / "trace").exists()
    assert not Path(env["UV_INSTALL_DIR"]).exists()
    assert not list(root.glob("heinzel-install.*"))


@pytest.mark.parametrize("problem", ["upstream-failure", "missing-binary", "nonexecutable-binary"])
def test_upstream_failure_or_missing_executable_never_launches_heinzel_and_cleans_up(machine, problem):
    root, _, _, env = machine
    downloader(machine, "curl")
    upstream = Path(env["UV_FAKE_INSTALLER"])
    if problem == "upstream-failure":
        upstream.write_text("exit 23\n")
    elif problem == "missing-binary":
        upstream.write_text("exit 0\n")
    else:
        upstream.write_text('mkdir -p "$UV_INSTALL_DIR"\nprintf "not executable" > "$UV_INSTALL_DIR/uv"\n')
    result = invoke(machine)
    assert result.returncode == (23 if problem == "upstream-failure" else 1)
    assert not (root / "trace").exists()
    assert not list(root.glob("heinzel-install.*"))


def test_missing_downloader_is_actionable_and_has_no_side_effects(machine):
    root, _, _, _ = machine
    result = invoke(machine)
    assert result.returncode == 1
    assert "Install curl or wget" in result.stderr
    assert not (root / "trace").exists()


def test_windows_is_explicitly_unsupported_before_any_installation(machine):
    root, _, _, env = machine
    env["TEST_PLATFORM"] = "MINGW64_NT"
    result = invoke(machine)
    assert result.returncode == 1
    assert "macOS and Linux" in result.stderr
    assert "Windows is not supported" in result.stderr
    assert not (root / "trace").exists()
