from datetime import timedelta

import pytest

from heinzel import HeinzelError, Module, Settings
from heinzel.heads.daemon import serve, tick_operation

pytestmark = pytest.mark.unit


def ticking(results: list[object]) -> tuple[Module, Module]:
    system = Module("system", "System.")
    daemon = Module("daemon", "Daemon.")

    @daemon.operation(read_only=False)
    def tick() -> str:
        """Tick once."""
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return str(result)

    return system, daemon


def test_serve_ticks_every_interval_and_reports_results_and_errors(make_kernel):
    system, daemon = ticking(["first", HeinzelError("ledger broken"), "third"])
    reported: list[object] = []
    slept: list[float] = []
    serve(
        make_kernel(system=system, system__daemon=daemon),
        timedelta(minutes=5),
        reported.append,
        ticks=3,
        sleep=slept.append,
    )
    assert [str(r) for r in reported] == ["first", "ledger broken", "third"]
    assert isinstance(reported[1], HeinzelError)
    assert slept == [300.0, 300.0]


def test_serve_refuses_when_the_daemon_module_is_disabled(make_kernel, settings: Settings):
    system, daemon = ticking([])
    disabled = settings.model_copy(update={"disabled_modules": frozenset({"system.daemon"})})
    kernel = make_kernel(settings_=disabled, system=system, system__daemon=daemon)
    with pytest.raises(HeinzelError, match=r"system\.daemon module is disabled"):
        tick_operation(kernel)
