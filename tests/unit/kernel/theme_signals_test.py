"""Signals themes follow (ADR 0060): the daypart from the clock and the sun, and what a module publishes."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from heinzel import Module, OperationLet, SignalLet, ThemeSignals
from heinzel.kernel import DAYPARTS, WEATHERS, daypart

pytestmark = pytest.mark.unit

BERLIN = ZoneInfo("Europe/Berlin")


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 21, hour, minute, tzinfo=BERLIN)


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (5, 0, "night"),
        (6, 0, "dawn"),
        (7, 14, "dawn"),
        (7, 15, "morning"),
        (11, 0, "day"),
        (17, 29, "day"),
        (17, 30, "dusk"),
        (18, 59, "dusk"),
        (19, 0, "evening"),
        (22, 30, "night"),
        (23, 59, "night"),
    ],
)
def test_the_daypart_follows_the_default_sun(hour, minute, expected):
    assert daypart(at(hour, minute)) == expected


def test_the_daypart_follows_the_sun_a_module_knows_in_any_zone():
    sunrise = datetime(2026, 6, 21, 3, 0, tzinfo=UTC)  # 05:00 in Berlin
    sunset = datetime(2026, 6, 21, 19, 30, tzinfo=UTC)  # 21:30 in Berlin
    assert daypart(at(4, 30), sunrise, sunset) == "dawn"
    assert daypart(at(6, 0), sunrise, sunset) == "morning"
    assert daypart(at(20, 45), sunrise, sunset) == "dusk"
    assert daypart(at(22, 15), sunrise, sunset) == "evening"


def test_sun_times_out_of_order_fall_back_to_the_default():
    sunrise = datetime(2026, 6, 21, 21, 0, tzinfo=BERLIN)
    sunset = datetime(2026, 6, 21, 4, 0, tzinfo=BERLIN)
    assert daypart(at(12), sunrise, sunset) == daypart(at(12))
    assert daypart(at(12), datetime(2026, 6, 21, 0, 10, tzinfo=BERLIN)) == daypart(at(12))


def test_the_vocabulary_is_closed():
    assert DAYPARTS == ("dawn", "morning", "day", "dusk", "evening", "night")
    assert "rain" in WEATHERS
    with pytest.raises(ValidationError):
        ThemeSignals.model_validate({"weather": "sunny"})
    with pytest.raises(ValidationError):
        ThemeSignals.model_validate({"mood": "happy"})
    with pytest.raises(ValidationError):
        ThemeSignals.model_validate({"sunrise": "2026-06-21T05:00:00"})  # no zone, no meaning


def test_module_signals_is_a_read_only_operation_and_a_signal_let():
    module = Module("weather", "The weather.")

    @module.signals(every="15m")
    def now() -> ThemeSignals:
        """The weather now, for themes."""
        return ThemeSignals(weather="clear")

    (operation,) = [let for let in module.lets if isinstance(let, OperationLet)]
    (signal,) = [let for let in module.lets if isinstance(let, SignalLet)]
    assert (operation.name, operation.read_only, operation.func) == ("now", True, now)
    assert (signal.name, signal.func, signal.every) == ("now", now, timedelta(minutes=15))


@pytest.mark.parametrize("every", ["30s", "2d"])
def test_signals_refresh_every_minute_to_a_day(every):
    module = Module("weather", "The weather.")
    with pytest.raises(ValueError, match="1m to 1d"):

        @module.signals(every=every)
        def now() -> ThemeSignals:
            """The weather now."""
            return ThemeSignals()


def test_a_northern_summer_stretches_the_day_instead_of_dropping_the_sun():
    sunrise = datetime(2026, 6, 21, 3, 50, tzinfo=BERLIN)
    sunset = datetime(2026, 6, 21, 22, 15, tzinfo=BERLIN)
    assert daypart(at(11, 0), sunrise, sunset) == "day"
    assert daypart(at(20, 0), sunrise, sunset) == "day"
    assert daypart(at(21, 30), sunrise, sunset) == "dusk"
    assert daypart(at(22, 50), sunrise, sunset) == "evening"
    assert daypart(at(23, 20), sunrise, sunset) == "night"
    late = datetime(2026, 6, 21, 23, 50, tzinfo=BERLIN)  # night would start tomorrow: the defaults apply
    assert daypart(at(20, 0), sunrise, late) == daypart(at(20, 0))
