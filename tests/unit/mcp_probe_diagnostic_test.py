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


@pytest.mark.parametrize(
    ("workers", "expected"),
    [
        (("gw0", "gw1", "gw2"), "distinct"),
        (("gw0", "gw1", "gw1"), "not_distinct"),
        (("gw0", "gw1", "private-secret"), "unavailable"),
    ],
)
def test_worker_receipt_requires_three_distinct_workers(tmp_path, workers, expected):
    overlap_spec = importlib.util.spec_from_file_location(
        "mcp_diagnostic_overlap_workers",
        Path(__file__).resolve().parents[2] / "scripts/mcp_probe_diagnostic/overlap.py",
    )
    assert overlap_spec
    assert overlap_spec.loader
    overlap = importlib.util.module_from_spec(overlap_spec)
    overlap_spec.loader.exec_module(overlap)
    assert overlap.worker_receipt(tmp_path) == "unavailable"
    for case, worker in zip(("true", "false", "companion"), workers, strict=True):
        (tmp_path / f"worker.{case}").write_text(worker)
    assert overlap.worker_receipt(tmp_path) == expected


def test_timeline_requires_unique_ordered_allowlisted_markers_and_correlated_fake(tmp_path):
    timeline_spec = importlib.util.spec_from_file_location(
        "mcp_timeline", Path(__file__).resolve().parents[2] / "scripts/mcp_probe_diagnostic/timeline.py"
    )
    assert timeline_spec
    assert timeline_spec.loader
    timeline = importlib.util.module_from_spec(timeline_spec)
    timeline_spec.loader.exec_module(timeline)
    path = tmp_path / "events"
    labels = [
        "launch.before",
        "launch.after",
        "communicate.before",
        "child.entry",
        "child.import_done",
        "client.enter.start",
        "client.enter.ok",
        "transport.enter.start",
        "transport.enter.ok",
        "session.enter.start",
        "session.enter.ok",
        "init.start",
        "fake.server.401_sent",
        "client.401",
        "init.error",
        "session.exit.start",
        "session.exit.error",
        "transport.exit.start",
        "transport.exit.error",
        "client.exit.start",
        "client.exit.ok",
        "observe.exit",
        "communicate.after",
    ]

    def write(entries):
        path.write_text("".join(f"{name} {index + 1}\n" for index, name in enumerate(entries)))

    write(labels)
    status, events = timeline.parse_timeline(path, fake=True)
    assert status == "complete"
    assert all("private" not in event for event, _ in events)
    write([name for name in labels if name != "client.401"])
    assert timeline.parse_timeline(path, fake=True)[0] == "incomplete"
    write([*labels, "client.401"])
    assert timeline.parse_timeline(path, fake=True)[0] == "invalid"
    write([*labels, "private_secret"])
    assert timeline.parse_timeline(path, fake=True) == ("invalid", [])
    reordered = labels.copy()
    reordered[3], reordered[4] = reordered[4], reordered[3]
    write(reordered)
    assert timeline.parse_timeline(path, fake=True)[0] == "invalid"
    early_exit = labels.copy()
    early_exit.remove("observe.exit")
    early_exit.insert(early_exit.index("init.start"), "observe.exit")
    write(early_exit)
    assert timeline.parse_timeline(path, fake=True)[0] == "invalid"
    late_inner_exit = labels.copy()
    late_inner_exit.remove("session.exit.error")
    late_inner_exit.insert(late_inner_exit.index("transport.exit.start") + 1, "session.exit.error")
    write(late_inner_exit)
    assert timeline.parse_timeline(path, fake=True)[0] == "invalid"
    write([*labels, "list.start"])
    assert timeline.parse_timeline(path, fake=True)[0] == "invalid"
    path.write_bytes(b"x" * 8193)
    assert timeline.parse_timeline(path, fake=True) == ("invalid", [])


def test_context_proxy_preserves_suppression_and_cancellation(monkeypatch):
    import asyncio

    bootstrap_spec = importlib.util.spec_from_file_location(
        "mcp_diagnostic_bootstrap", Path(__file__).resolve().parents[2] / "scripts/mcp_probe_diagnostic/bootstrap.py"
    )
    assert bootstrap_spec
    assert bootstrap_spec.loader
    bootstrap = importlib.util.module_from_spec(bootstrap_spec)
    bootstrap_spec.loader.exec_module(bootstrap)
    events = []
    monkeypatch.setattr(bootstrap, "emit", events.append)
    cancelled = asyncio.CancelledError("private")

    class Original:
        async def __aenter__(self):
            return "value"

        async def __aexit__(self, *args):
            assert args == (None, None, None)
            return False

    async def normal():
        proxy = bootstrap.ContextProxy(Original())
        assert await proxy.__aenter__() == "value"
        assert await proxy.__aexit__(None, None, None) is False

    asyncio.run(normal())
    assert events == ["transport.enter.start", "transport.enter.ok", "transport.exit.start", "transport.exit.ok"]

    class Cancelled:
        async def __aenter__(self):
            raise cancelled

    async def failing():
        with pytest.raises(asyncio.CancelledError) as seen:
            await bootstrap.ContextProxy(Cancelled()).__aenter__()
        assert seen.value is cancelled

    events.clear()
    asyncio.run(failing())
    assert events == ["transport.enter.start", "transport.enter.error"]


@pytest.mark.parametrize("failure", ["open", "bootstrap"])
def test_marker_setup_failure_falls_back_before_original_spawn(monkeypatch, tmp_path, failure):
    import os
    import subprocess
    import threading
    from types import SimpleNamespace

    plugin_spec = importlib.util.spec_from_file_location(
        "mcp_plugin_fallback", Path(__file__).resolve().parents[2] / "scripts/mcp_probe_diagnostic/vnxd_plugin.py"
    )
    assert plugin_spec
    assert plugin_spec.loader
    plugin = importlib.util.module_from_spec(plugin_spec)
    plugin_spec.loader.exec_module(plugin)
    monkeypatch.setenv("VNXD_PHASE_DIR", str(tmp_path))
    calls = []

    class Original:
        pid = 123

        def __init__(self, args, *a, **kw):
            calls.append((args, kw))

        def communicate(self, *a, **kw):
            calls.append((a, kw))
            return ("", None)

    monkeypatch.setattr(subprocess, "Popen", Original)
    real_open = os.open
    real_read = Path.read_text
    opened = []

    def fail_marker(path, *args, **kwargs):
        is_marker = str(path).endswith(".events")
        if is_marker and failure == "open":
            raise OSError
        fd = real_open(path, *args, **kwargs)
        if is_marker:
            opened.append(fd)
        return fd

    def fail_bootstrap(path, *args, **kwargs):
        if path.name == "bootstrap.py" and failure == "bootstrap":
            raise OSError
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", fail_marker)
    monkeypatch.setattr(Path, "read_text", fail_bootstrap)

    def fail_thread(_thread):
        raise RuntimeError

    monkeypatch.setattr(threading.Thread, "start", fail_thread)
    hook = plugin.pytest_runtest_call(SimpleNamespace(nodeid=plugin.TARGET + "[False]"))
    next(hook)
    argv = ["python", "-I", "-m", plugin.PROBE, "http://127.0.0.1:123/mcp"]
    try:
        process = subprocess.Popen(argv, env={"PATH": "/bin"})
        process.communicate(input="hzmcp_synthetic")
    finally:
        with pytest.raises(StopIteration):
            next(hook)
    assert calls[0] == (argv, {"env": {"PATH": "/bin"}})
    assert calls[1][1]["input"] == "hzmcp_synthetic"
    if failure == "open":
        assert not (tmp_path / "false.events").exists()
    else:
        assert len(opened) == 1
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(opened[0])


def test_fake_control_requires_every_identity_matched_phase(tmp_path):
    del tmp_path
    timeline_spec = importlib.util.spec_from_file_location(
        "mcp_timeline_fake", Path(__file__).resolve().parents[2] / "scripts/mcp_probe_diagnostic/timeline.py"
    )
    assert timeline_spec
    assert timeline_spec.loader
    timeline = importlib.util.module_from_spec(timeline_spec)
    timeline_spec.loader.exec_module(timeline)
    phases = {
        phase: f"VNXD case=fake phase={phase} outcome=passed kind=none duration_ms=1"
        for phase in ("setup", "call", "teardown")
    }
    assert timeline.fake_control_complete(0, "complete", phases)
    assert not timeline.fake_control_complete(1, "complete", phases)
    assert not timeline.fake_control_complete(0, "incomplete", phases)
    missing = dict(phases)
    del missing["setup"]
    assert not timeline.fake_control_complete(0, "complete", missing)
    wrong = dict(phases, call=phases["call"].replace("case=fake", "case=true"))
    assert not timeline.fake_control_complete(0, "complete", wrong)
    wrong_phase = dict(phases, call=phases["setup"])
    assert not timeline.fake_control_complete(0, "complete", wrong_phase)
    failed_phase = dict(phases, call=phases["call"].replace("outcome=passed", "outcome=failed"))
    assert not timeline.fake_control_complete(0, "complete", failed_phase)
