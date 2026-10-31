"""The e2e failure report observes the selected interpreter, not the shell shim."""

import os
import stat

import pytest

from tests.e2e._interpreter_diagnostics import interpreter_diagnostics

pytestmark = pytest.mark.unit


def test_reports_resolved_python_and_parent_metadata(tmp_path):
    external = tmp_path / "external"
    external.mkdir(mode=0o700)
    python = external / "python"
    python.write_bytes(b"inert")
    bin_dir = tmp_path / "tool/bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").symlink_to(python)

    result = interpreter_diagnostics(tmp_path / "tool")
    info = external.stat()
    assert f"selected tool python: {python}" in result
    assert f"current uid: {os.getuid()}" in result
    assert f"python ancestor: {external} uid={info.st_uid} mode={stat.S_IMODE(info.st_mode):04o}" in result


def test_missing_python_does_not_hide_original_failure(tmp_path):
    assert interpreter_diagnostics(tmp_path) == "tool python inspection failed: FileNotFoundError"


def test_symlink_loop_does_not_hide_original_failure(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").symlink_to("python")
    assert interpreter_diagnostics(tmp_path) in {
        "tool python inspection failed: RuntimeError",
        "tool python inspection failed: OSError",
    }
