"""The start screen reports the real web server state."""

import json
from types import SimpleNamespace

import pytest

from heinzel.modules.ui import _overview
from heinzel.modules.ui._service import LABEL

pytestmark = pytest.mark.unit


def _record(state, port=8737):
    directory = state / "ui"
    directory.mkdir(parents=True, mode=0o700)
    state.chmod(0o700)
    path = directory / "service.json"
    path.write_text(json.dumps({"port": port}))
    path.chmod(0o600)


def _manager(monkeypatch, stdout, code=0):
    monkeypatch.setattr(
        _overview,
        "bounded_manager_command",
        lambda *a, **k: SimpleNamespace(stdout=stdout, returncode=code, truncated=False),
    )


def test_running_reports_port(tmp_path, monkeypatch):
    _record(tmp_path)
    _manager(monkeypatch, f"PID\tStatus\tLabel\n12\t0\t{LABEL}\n")
    seen = _overview.observe(tmp_path, "darwin")
    assert (seen.state, seen.detail) == ("running", "port 8737")


def test_stopped_when_not_loaded(tmp_path, monkeypatch):
    _record(tmp_path)
    _manager(monkeypatch, "PID\tStatus\tLabel\n12\t0\tother\n")
    assert _overview.observe(tmp_path, "darwin").state == "stopped"


def test_not_installed_without_record(tmp_path):
    seen = _overview.observe(tmp_path, "darwin")
    assert seen.state == "not installed"
    assert "hz ui install" in seen.detail


def test_unknown_when_manager_fails(tmp_path, monkeypatch):
    _record(tmp_path)
    _manager(monkeypatch, "", code=1)
    assert _overview.observe(tmp_path, "darwin").state == "unknown"


def test_unknown_on_unsupported_platform(tmp_path):
    assert _overview.observe(tmp_path, "win32").state == "unknown"
