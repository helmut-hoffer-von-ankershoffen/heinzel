"""The one-off MCP diagnostic accepts only its explicitly pinned dispatch."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "mcp_diagnostic_guard", Path(__file__).resolve().parents[2] / "scripts/mcp_probe_diagnostic/guard.py"
)
assert spec
assert spec.loader
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
authorized = guard.authorized

pytestmark = pytest.mark.unit

SHA = "a" * 40


def request():
    return {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/ci/mcp-macos-diagnostic",
        "FOCUSED_REF": "ci/mcp-macos-diagnostic",
        "REQUESTED_OS": "macos-latest",
        "REQUESTED_KIND": "pytest",
        "REQUESTED_REPEAT": "1",
        "REQUESTED_STRESS": "false",
        "EXPECTED_SHA": SHA,
        "GITHUB_SHA": SHA,
    }


def test_authorizes_only_the_exact_dispatched_checkout():
    assert authorized(request(), SHA)
    assert not authorized(request(), "b" * 40)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("GITHUB_EVENT_NAME", "push"),
        ("GITHUB_REF", "refs/heads/main"),
        ("FOCUSED_REF", "main"),
        ("REQUESTED_OS", "ubuntu-24.04"),
        ("REQUESTED_KIND", "e2e"),
        ("REQUESTED_REPEAT", "2"),
        ("REQUESTED_STRESS", "true"),
        ("EXPECTED_SHA", "a" * 39),
        ("EXPECTED_SHA", "A" * 40),
        ("GITHUB_SHA", "b" * 40),
    ],
)
def test_refuses_wrong_scope(key, value):
    env = request()
    env[key] = value
    assert not authorized(env, SHA)


def test_sample_classification_is_allowlisted_and_discards_content():
    plugin_spec = importlib.util.spec_from_file_location(
        "mcp_diagnostic_plugin", Path(__file__).resolve().parents[2] / "scripts/mcp_probe_diagnostic/vnxd_plugin.py"
    )
    assert plugin_spec
    assert plugin_spec.loader
    plugin = importlib.util.module_from_spec(plugin_spec)
    plugin_spec.loader.exec_module(plugin)
    assert plugin.classify(b"secret-fixture kevent recv") == "network_wait"
    assert plugin.classify(b"secret-fixture unknown") == "other"


def test_probe_continues_if_monitor_thread_cannot_start(monkeypatch, tmp_path):
    import subprocess
    import sys
    import threading
    from types import SimpleNamespace

    plugin_spec = importlib.util.spec_from_file_location(
        "mcp_diagnostic_plugin_no_thread",
        Path(__file__).resolve().parents[2] / "scripts/mcp_probe_diagnostic/vnxd_plugin.py",
    )
    assert plugin_spec
    assert plugin_spec.loader
    plugin = importlib.util.module_from_spec(plugin_spec)
    plugin_spec.loader.exec_module(plugin)
    monkeypatch.setenv("VNXD_PHASE_DIR", str(tmp_path))

    def fail_start(_thread):
        raise RuntimeError

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    original = subprocess.Popen
    hook = plugin.pytest_runtest_call(SimpleNamespace(nodeid=plugin.TARGET + "[True]"))
    next(hook)
    try:
        subprocess.run(
            [sys.executable, "-I", "-m", "heinzel.heads.mcp._http_probe"],
            capture_output=True,
            timeout=4,
            check=False,
        )
    finally:
        with pytest.raises(StopIteration):
            next(hook)
    assert subprocess.Popen is original
    assert "sample=unavailable" in (tmp_path / "true").read_text()


@pytest.mark.parametrize("fail", [False, True])
def test_sample_report_is_private_and_removed_on_success_or_failure(tmp_path, fail):
    import subprocess
    import threading

    plugin_spec = importlib.util.spec_from_file_location(
        "mcp_diagnostic_plugin_report",
        Path(__file__).resolve().parents[2] / "scripts/mcp_probe_diagnostic/vnxd_plugin.py",
    )
    assert plugin_spec
    assert plugin_spec.loader
    plugin = importlib.util.module_from_spec(plugin_spec)
    plugin_spec.loader.exec_module(plugin)
    tmp_path.chmod(0o700)
    reports = []

    class Sampler:
        returncode = 0

        def communicate(self, timeout=None):
            if fail and timeout:
                msg = "sample"
                raise subprocess.TimeoutExpired(msg, timeout)
            return (None, None)

        def poll(self):
            return None if fail else 0

        def kill(self):
            self.returncode = -9

    def launch(args, *, stdout, stderr):
        assert args[:5] == ["/usr/bin/sample", "123", "1", "1", "-file"]
        assert stdout == stderr == subprocess.DEVNULL
        path = Path(args[5])
        assert path.parent.parent == tmp_path
        assert path.parent.stat().st_mode & 0o077 == 0
        path.write_text("synthetic-private kevent")
        reports.append(path)
        return Sampler()

    observed = {"sample": "unavailable", "sampler": None}
    plugin.collect_sample(123, tmp_path, threading.Event(), threading.Lock(), observed, launcher=launch)
    assert observed["sample"] == ("unavailable" if fail else "wait")
    assert len(reports) == 1
    assert not reports[0].exists()
    assert not reports[0].parent.exists()
    assert list(tmp_path.iterdir()) == []
