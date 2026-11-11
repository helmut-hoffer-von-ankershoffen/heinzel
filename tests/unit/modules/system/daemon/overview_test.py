"""The overview is bounded and never equates a loaded timer with active work."""

import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from heinzel.kernel import ProcessOutcome
from heinzel.modules.system.daemon import _overview
from heinzel.modules.system.daemon._units import systemd_paths

pytestmark = pytest.mark.unit


def test_absent_units_do_not_run_a_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    run = Mock()
    monkeypatch.setattr(_overview, "bounded_manager_command", run)
    assert _overview.observe(tmp_path, tmp_path, "linux").state == "not installed"
    run.assert_not_called()


def test_loaded_timer_distinguishes_idle_tick_and_pause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for path in systemd_paths(tmp_path):
        path.touch()
    run = Mock(return_value=ProcessOutcome(0, "", ""))
    monkeypatch.setattr(_overview, "bounded_manager_command", run)
    assert _overview.observe(tmp_path, tmp_path, "linux").detail == (
        "scheduler loaded; current tick unknown; last scheduled tick unknown"
    )
    assert 0 < run.call_args.kwargs["deadline"] - time.monotonic() <= 0.2
    assert set(run.call_args.kwargs["env"]) == {"PATH", "LANG", "XDG_RUNTIME_DIR"}
    (tmp_path / "paused").touch()
    assert _overview.observe(tmp_path, tmp_path, "linux").detail == "scheduler loaded; jobs paused"


def test_manager_timeout_is_unknown_and_inactive_is_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for path in systemd_paths(tmp_path):
        path.touch()
    run = Mock(return_value=ProcessOutcome(None, "", ""))
    monkeypatch.setattr(_overview, "bounded_manager_command", run)
    assert _overview.observe(tmp_path, tmp_path, "linux").state == "unknown"
    run.return_value = ProcessOutcome(3, "", "")
    assert _overview.observe(tmp_path, tmp_path, "linux").state == "stopped"


def test_partial_install_is_unhealthy_without_running_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    systemd_paths(tmp_path)[0].touch()
    run = Mock()
    monkeypatch.setattr(_overview, "bounded_manager_command", run)
    assert _overview.observe(tmp_path, tmp_path, "linux").state == "unhealthy"
    run.assert_not_called()


def test_macos_manager_output_requires_valid_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from heinzel.modules.system.daemon._units import LABEL, plist_path

    plist_path(tmp_path).touch()
    run = Mock(return_value=ProcessOutcome(0, f"PID\tStatus\tLabel\n-\t0\t{LABEL}\n", ""))
    monkeypatch.setattr(_overview, "bounded_manager_command", run)
    assert _overview.observe(tmp_path, tmp_path, "darwin").state == "running"
    run.return_value = ProcessOutcome(0, "", "")
    assert _overview.observe(tmp_path, tmp_path, "darwin").state == "unknown"


def test_builtin_registration_exposes_local_observer(make_kernel):
    from heinzel.kernel import DaemonOverview

    kernel = make_kernel(builtins=True)
    assert (
        kernel.resolve_builtin_service(DaemonOverview, "system.daemon").observe(time.monotonic() + 0.2).state
        == "not installed"
    )


def test_fresh_failed_stale_and_unverified_history_are_distinct(tmp_path: Path):
    import json
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    path = tmp_path / "heartbeat.json"
    path.write_text(
        json.dumps({"source": "scheduled", "completed_at": now.isoformat(), "interval_minutes": 5, "outcome": "failed"})
    )
    path.chmod(0o600)
    assert _overview._history(tmp_path, now) == ("last scheduled tick failed (0m ago; every 5m)", True)
    assert _overview._history(tmp_path, now + timedelta(minutes=11)) == (
        "last scheduled tick stale (11m ago; every 5m)",
        True,
    )
    assert _overview._history(tmp_path, now - timedelta(seconds=1)) == ("last scheduled tick unknown", False)
    path.write_text(
        json.dumps({"source": "manual", "completed_at": now.isoformat(), "interval_minutes": 5, "outcome": "failed"})
    )
    assert _overview._history(tmp_path, now) == ("last scheduled tick unknown", False)
    path.chmod(0o644)
    assert _overview._history(tmp_path, now) == ("last scheduled tick unknown", False)


def test_overview_never_contends_with_the_scheduler_tick_lock(tmp_path, monkeypatch):
    import fcntl
    import os

    for path in systemd_paths(tmp_path):
        path.touch()
    os.mkfifo(tmp_path / "tick.lock", 0o600)
    monkeypatch.setattr(_overview, "bounded_manager_command", lambda *args, **kwargs: ProcessOutcome(0, "", ""))

    def fail(*args, **kwargs):
        pytest.fail("overview must not contend with the actual execution lock")

    monkeypatch.setattr(fcntl, "flock", fail)
    assert "current tick unknown" in _overview.observe(tmp_path, tmp_path, "linux").detail


def test_deeply_nested_history_is_unknown(tmp_path):
    from datetime import UTC, datetime

    tmp_path.chmod(0o700)
    path = tmp_path / "heartbeat.json"
    path.write_text("[" * 1500 + "]" * 1500)
    path.chmod(0o600)
    assert _overview._history(tmp_path, datetime.now(UTC)) == ("last scheduled tick unknown", False)


def test_timed_out_manager_child_is_terminated_before_observation_returns(monkeypatch):
    import subprocess
    import sys

    from heinzel.modules.system.daemon import _service

    children = []
    original = subprocess.Popen

    def start(*args, **kwargs):
        process = original(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(_service.subprocess, "Popen", start)
    result = _service.bounded_manager_command(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        deadline=time.monotonic() + 0.02,
        env={"PATH": "/usr/bin:/bin", "LANG": "C"},
    )
    assert result.returncode is None
    assert len(children) == 1
    assert children[0].poll() is not None
