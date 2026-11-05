"""Copied recovery keeps the original external-interpreter authority, including root-owned system Python."""

import os
import stat
from pathlib import Path

import pytest

from heinzel.kernel import _installation_continuity as continuity
from heinzel.kernel import _uninstall_recovery as recovery

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("system", [False, True])
def test_recovery_interpreter_digest_matches_initial_capture_without_execution(tmp_path, monkeypatch, system):
    python = Path("/usr/bin/python3").resolve(strict=True) if system else tmp_path / "python"
    if system:
        assert python.stat().st_uid == 0
    else:
        python.write_bytes(b"isolated interpreter fixture")
        python.chmod(0o700)
    expected = continuity.external_python(python)[1]
    monkeypatch.setattr(recovery.subprocess, "run", lambda *_a, **_k: pytest.fail("Do not execute the interpreter"))
    monkeypatch.setattr(recovery.subprocess, "Popen", lambda *_a, **_k: pytest.fail("Do not execute the interpreter"))
    assert recovery.python_continuity(python) == expected
    if system:
        # An interpreter's root ownership never authorizes root-owned journals, gates or service files.
        with pytest.raises(ValueError, match="not private"):
            recovery.read(python)


@pytest.mark.parametrize(
    "change", ["link", "parent-link", "writable", "parent-writable", "not-executable", "directory", "foreign"]
)
def test_interpreter_reader_refuses_unsafe_authority(tmp_path, monkeypatch, change):
    directory = tmp_path / "python-home"
    directory.mkdir(mode=0o700)
    python = directory / "python"
    python.write_bytes(b"isolated interpreter fixture")
    python.chmod(0o700)
    if change == "link":
        link = directory / "alias"
        link.symlink_to(python)
        python = link
    elif change == "parent-link":
        link = tmp_path / "alias"
        link.symlink_to(directory, target_is_directory=True)
        python = link / "python"
    elif change == "writable":
        python.chmod(0o722)
    elif change == "parent-writable":
        directory.chmod(0o722)
    elif change == "not-executable":
        python.chmod(0o600)
    elif change == "directory":
        python.unlink()
        python.mkdir(mode=0o700)
    else:
        monkeypatch.setattr(recovery.os, "getuid", lambda: python.stat().st_uid + 10000)
    with pytest.raises(ValueError, match="interpreter"):
        recovery.python_continuity(python)


def test_interpreter_change_during_read_is_refused(tmp_path, monkeypatch):
    python = tmp_path / "python"
    python.write_bytes(b"isolated interpreter fixture")
    python.chmod(0o700)
    original = os.read

    def changed(fd, count):
        content = original(fd, count)
        python.chmod(0o500)
        return content

    monkeypatch.setattr(recovery.os, "read", changed)
    with pytest.raises(ValueError, match="interpreter"):
        recovery.python_continuity(python)


def test_oversized_interpreter_is_refused_before_read(tmp_path, monkeypatch):
    python = tmp_path / "python"
    with python.open("wb") as stream:
        stream.truncate(128 * 1024 * 1024 + 1)
    python.chmod(stat.S_IRUSR | stat.S_IXUSR)
    monkeypatch.setattr(recovery.os, "read", lambda *_: pytest.fail("Oversized files must not be read"))
    with pytest.raises(ValueError, match="interpreter"):
        recovery.python_continuity(python)
