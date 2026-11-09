"""The update path finds uv and gives it a usable PATH when started from Finder with launchd's PATH (bead cwte)."""

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from heinzel import Progress
from heinzel.kernel import Installation

pytestmark = [pytest.mark.integration, pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell fixture")]


def test_installation_runs_uv_from_a_homebrew_like_dir_with_a_minimal_path(tmp_path, monkeypatch):
    brew = tmp_path / "opt/homebrew/bin"
    brew.mkdir(parents=True)
    seen = tmp_path / "seen"
    uv = brew / "uv"
    uv.write_text(f'#!/bin/sh\nprintf "%s\\n" "$PATH" > "{seen}"\necho "uv 0.0.0"\n')
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("UV_INSTALL_DIR", str(brew))

    installation = Installation(Progress(), prefix=tmp_path / "tool")
    assert installation.uv == str(uv)
    result = installation._runner([installation.uv, "--version"])

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "uv 0.0.0"
    child_path = Path(seen).read_text().strip().split(os.pathsep)
    assert child_path[:2] == ["/usr/bin", "/bin"]
    assert str(brew) in child_path
    assert "/opt/homebrew/bin" in child_path


def test_missing_uv_says_where_it_looked(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent-a:/nonexistent-b")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("UV_INSTALL_DIR", raising=False)
    monkeypatch.delenv("XDG_BIN_HOME", raising=False)
    monkeypatch.delenv("CARGO_HOME", raising=False)
    monkeypatch.setattr("heinzel.kernel._uv_locate.standard_directories", lambda _=None: [str(tmp_path / ".local/bin")])
    from heinzel import HeinzelError

    with pytest.raises(HeinzelError) as caught:
        _ = Installation(Progress(), prefix=tmp_path).uv
    assert "/nonexistent-a" in str(caught.value)
    assert str(tmp_path / ".local/bin") in str(caught.value)


def test_desktop_owned_cli_inherits_finder_path_and_finds_home_uv_offline(tmp_path):
    """Exercise the desktop binding and child process; this simulates Finder, not a signed app launch."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the desktop controller fixture")
    home = tmp_path / "home"
    config = home / ".config/heinzel"
    owned_bin = home / ".local/bin"
    shell_bin = tmp_path / "shell-bin"
    for directory in (config, owned_bin, shell_bin):
        directory.mkdir(parents=True, mode=0o700)
    trace = tmp_path / "uv-used"
    for directory, name in ((owned_bin, "home"), (shell_bin, "shell")):
        uv = directory / "uv"
        uv.write_text(f"#!/bin/sh\nprintf '%s' {name} > {str(trace)!r}\n")
        uv.chmod(0o700)
    executable = tmp_path / "owned-hz"
    source = Path(__file__).resolve().parents[2] / "src"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import sys, time\n"
        f"sys.path.insert(0, {str(source)!r})\n"
        "from heinzel import Progress\n"
        "from heinzel.kernel import Installation\n"
        "assert sys.argv[1:] == ['ui', 'serve', '--port', '0', '--no-browser']\n"
        f"installation = Installation(Progress(), prefix=__import__('pathlib').Path({str(tmp_path / 'tool')!r}))\n"
        "assert installation._runner([installation.uv, '--version']).returncode == 0\n"
        "print('http://127.0.0.1:12345/#token=abcdefghijklmnopqrstuvwxyz0123456789', flush=True)\n"
        "time.sleep(30)\n"
    )
    executable.chmod(0o700)
    binding = config / "desktop-owned-cli.json"
    binding.write_text(json.dumps({"version": 1, "executable": str(executable)}))
    binding.chmod(0o600)
    module = (source.parent / "desktop/src/owned-cli.js").as_uri()
    script = (
        f"import {{ installedCli, createOwnedCli }} from {json.dumps(module)};\n"
        "const cli = createOwnedCli({executable: () => installedCli(), startupTimeoutMs: 5000});\n"
        "try { await cli.start(); console.log('ready'); } finally { await cli.stop(); }\n"
    )
    minimal = "/usr/bin:/bin:/usr/sbin:/sbin"
    for path, expected in ((minimal, "home"), (f"{shell_bin}:{minimal}", "shell")):
        trace.unlink(missing_ok=True)
        result = subprocess.run(
            [node, "--input-type=module", "-e", script],
            env={"HOME": str(home), "PATH": path},
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ready"
        assert trace.read_text() == expected

    # A broken home uv must not be mistaken for a working Finder update path.
    trace.unlink()
    (owned_bin / "uv").write_text(f"#!/bin/sh\nprintf '%s' broken > {str(trace)!r}\nexit 42\n")
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        env={"HOME": str(home), "PATH": minimal},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode != 0
    assert "ready" not in result.stdout
    assert trace.read_text() == "broken"
