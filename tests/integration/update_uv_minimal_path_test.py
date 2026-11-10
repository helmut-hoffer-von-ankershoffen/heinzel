"""The update path finds uv and gives it a usable PATH when started from Finder with launchd's PATH (bead cwte)."""

import os
import stat
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
