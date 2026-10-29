"""Machine samples are bounded and shared, including concurrent callers and missing measurements."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, local
from types import SimpleNamespace

import psutil
import pytest
from rich.console import Console

from heinzel.modules.system.resource import Resources, ResourceService, render

pytestmark = pytest.mark.unit
CPU_PERCENT = psutil.cpu_percent


@pytest.fixture
def probe(monkeypatch):
    from heinzel.modules.system import resource as resources

    monkeypatch.setattr(resources, "gethostname", lambda: "workshop.local")
    calls = []
    monkeypatch.setattr(resources.psutil, "cpu_percent", lambda *, interval: calls.append(interval) or 12.5)
    monkeypatch.setattr(resources.psutil, "virtual_memory", lambda: SimpleNamespace(percent=40.0))
    monkeypatch.setattr(resources.psutil, "getloadavg", lambda: (3.0, 2.0, 1.0))
    monkeypatch.setattr(resources.psutil, "cpu_count", lambda: 10)
    monkeypatch.setattr(resources, "monotonic", lambda: 10.0)
    monkeypatch.setattr(resources, "datetime", SimpleNamespace(now=lambda _: datetime(2026, 10, 25, tzinfo=UTC)))
    return calls


def test_concurrent_callers_share_one_sample(probe):
    service = ResourceService()
    with ThreadPoolExecutor(max_workers=8) as pool:
        samples = list(pool.map(lambda _: service.sample(), range(8)))
    assert all(sample is samples[0] for sample in samples)
    assert probe == [0.1]
    sample = samples[0]
    assert (sample.cpu_percent, sample.memory_percent, sample.load_1m, sample.logical_cores) == (12.5, 40, 3, 10)
    assert sample.sampled_at.utcoffset().total_seconds() == 0
    assert sample.hostname == "workshop.local"
    assert sample.problems == []
    console = Console(record=True)
    console.print(render(sample))
    output = console.export_text()
    assert "workshop.local" in output
    assert "CPU" in output


def test_expired_cache_collects_again(probe, monkeypatch):
    monkeypatch.setattr("heinzel.modules.system.resource.monotonic", lambda: 10.0)
    service = ResourceService()
    first = service.sample()
    monkeypatch.setattr("heinzel.modules.system.resource.monotonic", lambda: 12.0)
    assert service.sample() is not first
    assert probe == [0.1, 0.1]


@pytest.mark.parametrize("cores_raise", [False, True])
def test_unavailable_measurements_are_not_zeros(probe, monkeypatch, cores_raise):
    def unavailable(*args, **kwargs):
        raise OSError

    monkeypatch.setattr("heinzel.modules.system.resource.psutil.cpu_percent", unavailable)
    monkeypatch.setattr("heinzel.modules.system.resource.psutil.virtual_memory", unavailable)
    monkeypatch.setattr("heinzel.modules.system.resource.psutil.getloadavg", unavailable)
    monkeypatch.setattr(
        "heinzel.modules.system.resource.psutil.cpu_count", unavailable if cores_raise else lambda: None
    )
    sample = ResourceService().sample()
    assert (sample.cpu_percent, sample.memory_percent, sample.load_1m, sample.logical_cores) == (None, None, None, None)
    assert len(sample.problems) == 4
    assert sample.model_dump(mode="json")["cpu_percent"] is None
    console = Console(record=True)
    console.print(render(sample))
    assert "Unavailable" in console.export_text()


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, 101.0])
def test_invalid_percentages_are_unavailable_without_losing_other_metrics(probe, monkeypatch, bad):
    monkeypatch.setattr("heinzel.modules.system.resource.psutil.cpu_percent", lambda **_: bad)
    monkeypatch.setattr("heinzel.modules.system.resource.psutil.virtual_memory", lambda: SimpleNamespace(percent=bad))
    sample = ResourceService().sample()
    assert sample.cpu_percent is sample.memory_percent is None
    assert (sample.load_1m, sample.logical_cores) == (3.0, 10)
    assert sample.problems == ["CPU usage is unavailable.", "Memory usage is unavailable."]
    assert "NaN" not in sample.model_dump_json()


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0])
def test_invalid_load_and_zero_cores_are_unavailable(probe, monkeypatch, bad):
    monkeypatch.setattr("heinzel.modules.system.resource.psutil.getloadavg", lambda: (bad, 0, 0))
    monkeypatch.setattr("heinzel.modules.system.resource.psutil.cpu_count", lambda: 0)
    sample = ResourceService().sample()
    assert sample.load_1m is sample.logical_cores is None
    assert sample.problems == ["Load average is unavailable.", "Core count is unavailable."]


def test_actual_psutil_first_sample_uses_two_readings_on_each_request_thread(probe, monkeypatch):
    times = CPU_PERCENT.__globals__["_ntp"].scputimes
    state = local()
    ready = Barrier(2)
    waits = []

    def cpu_times():
        count = getattr(state, "calls", 0)
        state.calls = count + 1
        if count == 0:
            ready.wait(timeout=2)
        values = dict.fromkeys(times._fields, 0)
        return times(**{**values, "user": 10 * count, "idle": 100 + 10 * count})

    monkeypatch.setattr(psutil, "cpu_percent", CPU_PERCENT)
    monkeypatch.setattr(psutil, "_last_cpu_times", {})
    monkeypatch.setattr(psutil, "cpu_times", cpu_times)
    monkeypatch.setattr(psutil, "time", SimpleNamespace(sleep=waits.append))
    with ThreadPoolExecutor(max_workers=2) as pool:
        samples = list(pool.map(lambda _: ResourceService().sample(), range(2)))
    assert [sample.cpu_percent for sample in samples] == [50.0, 50.0]
    assert waits == [0.1, 0.1]


def test_resource_model_rejects_nonfinite_json_values():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Resources(
            hostname="workshop.local",
            sampled_at=datetime(2026, 10, 25, tzinfo=UTC),
            cpu_percent=float("nan"),
            memory_percent=1,
            load_1m=1,
            logical_cores=1,
            problems=[],
        )
