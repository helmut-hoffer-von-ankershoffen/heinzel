"""Session timing reveals aggregate work without printing test data or worker errors."""

import math
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def reporter(request):
    return request.config.pluginmanager.get_plugin(str(Path(__file__).resolve().parents[1] / "conftest.py"))


def test_worker_timing_measures_cpu_and_phases_from_session_start(monkeypatch, reporter):
    times = iter((10.0, 30.0))
    cpus = iter(
        (
            SimpleNamespace(user=2, system=3, children_user=5, children_system=7),
            SimpleNamespace(user=4, system=6, children_user=9, children_system=12),
        )
    )
    monkeypatch.setattr(reporter, "time", SimpleNamespace(monotonic=lambda: next(times)))
    monkeypatch.setattr(reporter, "os", SimpleNamespace(times=lambda: next(cpus)))
    config = SimpleNamespace(workeroutput={})
    timing = reporter._SessionTiming(config)
    for phase, duration in (("setup", 1.0), ("call", 4.0), ("teardown", 2.0)):
        timing.pytest_runtest_logreport(SimpleNamespace(when=phase, duration=duration))
    timing.pytest_sessionfinish(None, 2)
    assert config.workeroutput["heinzel_integration_timing"] == {
        "wall": 20.0,
        "cpu": 5,
        "child_cpu": 9,
        "phases": 7.0,
        "completed": 1,
    }


@pytest.fixture
def valid_timing():
    return {"completed": 2, "wall": 5.0, "cpu": 1.0, "child_cpu": 0.5, "phases": 4.0}


@pytest.mark.parametrize("worker", ["private-worker", "gw0\nSECRET", None])
def test_invalid_worker_ids_with_valid_metrics_are_never_emitted(capsys, reporter, valid_timing, worker):
    timing = reporter._SessionTiming(SimpleNamespace())
    node = SimpleNamespace(
        gateway=SimpleNamespace(id=worker),
        workeroutput={"heinzel_integration_timing": valid_timing},
    )
    timing.pytest_xdist_node_collection_finished(node, ["SECRET"])
    timing.pytest_testnodedown(node, "SECRET")
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("key", ["completed", "wall", "cpu", "child_cpu", "phases"])
@pytest.mark.parametrize("value", ["SECRET", math.nan, math.inf, -1, None, True])
def test_each_invalid_metric_is_rejected_independently(capsys, reporter, valid_timing, key, value):
    timing = reporter._SessionTiming(SimpleNamespace())
    valid_timing[key] = value
    node = SimpleNamespace(gateway=SimpleNamespace(id="gw0"), workeroutput={"heinzel_integration_timing": valid_timing})
    timing.pytest_testnodedown(node, "SECRET")
    assert capsys.readouterr().out == ""


def test_valid_output_contains_only_worker_ids_counts_and_numbers(capsys, reporter, valid_timing):
    timing = reporter._SessionTiming(SimpleNamespace())
    node = SimpleNamespace(gateway=SimpleNamespace(id="gw0"), workeroutput={"heinzel_integration_timing": valid_timing})
    timing.pytest_xdist_node_collection_finished(node, ["SECRET"])
    timing.pytest_testnodedown(node, "SECRET")
    output = capsys.readouterr().out
    assert "INTEGRATION collected worker=gw0 tests=1 controller_seconds=" in output
    assert (
        "INTEGRATION worker=gw0 completed=2 wall_seconds=5.000 cpu_seconds=1.000 "
        "child_cpu_seconds=0.500 phase_seconds=4.000"
    ) in output
    assert "SECRET" not in output


@pytest.mark.parametrize(
    "expression", ["unit", "integration", "e2e", "(unit or integration) and not perf", "(integration) and not perf"]
)
def test_timing_registers_only_for_the_existing_integration_matrix_expression(monkeypatch, reporter, expression):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    registered = []
    config = SimpleNamespace(
        getoption=lambda _name: expression,
        pluginmanager=SimpleNamespace(register=lambda plugin, name: registered.append(name)),
    )
    reporter.pytest_configure(config)
    assert ("heinzel-integration-session-timing" in registered) == (expression == "(integration) and not perf")
