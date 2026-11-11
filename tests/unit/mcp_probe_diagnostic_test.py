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


@pytest.mark.parametrize("case", ["True", "False"])
def test_probe_continues_if_monitor_thread_cannot_start(monkeypatch, tmp_path, case):
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
    hook = plugin.pytest_runtest_call(SimpleNamespace(nodeid=plugin.TARGET + f"[{case}]"))
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
    assert "sample=unavailable" in (tmp_path / case.lower()).read_text()
    if case == "False":
        assert int((tmp_path / "negative.start").read_text()) < int((tmp_path / "negative.end").read_text())


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


@pytest.mark.parametrize(
    ("outcome", "exception", "kind"),
    [
        ("passed", None, "none"),
        ("skipped", RuntimeError("private"), "none"),
        ("failed", AssertionError("private"), "assertion"),
        ("failed", ValueError("private"), "exception"),
        ("failed", None, "unknown"),
        ("failed", pytest.fail.Exception("Timeout (>5.0s) private"), "timeout"),
    ],
)
def test_phase_records_expose_only_fixed_failure_kind(outcome, exception, kind):
    plugin_spec = importlib.util.spec_from_file_location(
        "mcp_diagnostic_plugin_phases",
        Path(__file__).resolve().parents[2] / "scripts/mcp_probe_diagnostic/vnxd_plugin.py",
    )
    assert plugin_spec
    assert plugin_spec.loader
    plugin = importlib.util.module_from_spec(plugin_spec)
    plugin_spec.loader.exec_module(plugin)
    result = plugin.phase_record("false", "call", outcome, exception, 1.234)
    assert result == f"VNXD case=false phase=call outcome={outcome} kind={kind} duration_ms=1234"
    assert "private" not in result


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((1_000_000, 9_000_000, 3_000_000, 7_000_000), 4),
        ((1_000_000, 3_000_000, 3_000_000, 7_000_000), 0),
        ((1_000_000, 3_000_000, 5_000_000, 7_000_000), 0),
        ((3_000_000, 1_000_000, 3_000_000, 7_000_000), None),
        ((1_000_000, 3_000_000, "not-numeric", 7_000_000), None),
    ],
)
def test_overlap_receipt_requires_two_valid_overlapping_intervals(tmp_path, values, expected):
    overlap_spec = importlib.util.spec_from_file_location(
        "mcp_diagnostic_overlap", Path(__file__).resolve().parents[2] / "scripts/mcp_probe_diagnostic/overlap.py"
    )
    assert overlap_spec
    assert overlap_spec.loader
    overlap = importlib.util.module_from_spec(overlap_spec)
    overlap_spec.loader.exec_module(overlap)
    names = ("negative.start", "negative.end", "companion.start", "companion.end")
    assert overlap.overlap_milliseconds(tmp_path) is None
    for name, value in zip(names, values, strict=True):
        (tmp_path / name).write_text(str(value))
    assert overlap.overlap_milliseconds(tmp_path) == expected


def test_companion_records_a_bounded_synthetic_interval(monkeypatch, tmp_path):
    from types import SimpleNamespace

    companion_spec = importlib.util.spec_from_file_location(
        "mcp_diagnostic_companion",
        Path(__file__).resolve().parents[2] / "scripts/mcp_probe_diagnostic/companion_test.py",
    )
    assert companion_spec
    assert companion_spec.loader
    companion = importlib.util.module_from_spec(companion_spec)
    companion_spec.loader.exec_module(companion)
    times = iter((100, 200, 3_500_000_100, 3_500_000_200))
    monkeypatch.setattr(companion, "time", SimpleNamespace(monotonic_ns=lambda: next(times)))
    monkeypatch.setenv("VNXD_PHASE_DIR", str(tmp_path))
    companion.test_cpu_companion()
    assert (tmp_path / "companion.start").read_text() == "100"
    assert (tmp_path / "companion.end").read_text() == "3500000200"
