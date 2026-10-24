"""The weather module's service: place, units and conversion, against a fake adapter. Offline."""

import io
from datetime import UTC, date, datetime
from typing import ClassVar

import pytest
from rich.console import Console

from heinzel import (
    Config,
    CurrentWeather,
    DailyForecast,
    HeinzelError,
    Module,
    WeatherPlace,
    WeatherReport,
)
from heinzel.modules.weather import Units, WeatherService, WeatherSettings
from heinzel.modules.weather._render import render_outlook
from heinzel.modules.weather._service import MAX_DAYS, convert, units_for_locale

pytestmark = pytest.mark.unit


def report(days: int = 5) -> WeatherReport:
    return WeatherReport(
        place=WeatherPlace(name="Lisbon, Portugal", latitude=38.7, longitude=-9.1),
        current=CurrentWeather(
            observed_at=datetime(2026, 10, 25, 12, tzinfo=UTC),
            summary="light rain",
            temperature_c=20.0,
            feels_like_c=-10.0,
            humidity_percent=None,
            wind_kmh=16.09344,
            precipitation_mm=25.4,
        ),
        days=[
            DailyForecast(
                day=date(2026, 10, 1 + i),
                summary="fog",
                high_c=30.0,
                low_c=0.0,
                rain_chance=None,
                precipitation_mm=2.54,
            )
            for i in range(days)
        ],
    )


class Fake:
    """A weather adapter that remembers the places it was asked about."""

    asked: ClassVar[list[str]] = []

    def __init__(self, days: int = 5) -> None:
        self.days = days

    def report(self, place: str) -> WeatherReport:
        Fake.asked.append(place)
        return report(self.days)


def service(make_kernel, config: Config | None = None, *, bound: bool = True, **settings: object) -> WeatherService:
    provider = Module("fake", "Test-only weather adapter.")
    provider.provides("weather", modes={"read"}, hosts=[])(Fake)
    kernel = make_kernel(
        builtins=True,
        fake=provider,
        config=config
        or Config(files=(), data={"weather": settings, "capabilities": {"weather": "fake" if bound else "none"}}),
    )
    Fake.asked = []
    return kernel.resolve(WeatherService)


def test_the_report_is_converted_to_imperial_and_rounded():
    outlook = convert(report(), Units.IMPERIAL, 2, "fake")
    assert (outlook.temperature, outlook.feels_like, outlook.wind, outlook.precipitation) == (68.0, 14.0, 10.0, 1.0)
    assert (outlook.days[0].high, outlook.days[0].low, outlook.days[0].precipitation) == (86.0, 32.0, 0.1)
    assert len(outlook.days) == 2


def test_metric_stays_as_the_adapter_gave_it():
    outlook = convert(report(), Units.METRIC, 5, "fake")
    assert (outlook.temperature, outlook.wind, outlook.precipitation) == (20.0, 16.1, 25.4)
    assert (outlook.humidity_percent, outlook.days[0].rain_chance) == (None, None)


@pytest.mark.parametrize(
    ("name", "units"),
    [
        ("en_US.UTF-8", Units.IMPERIAL),
        ("en-LR", Units.IMPERIAL),
        ("my_MM", Units.IMPERIAL),
        ("de_DE.UTF-8", Units.METRIC),
        ("C", Units.METRIC),
        ("", Units.METRIC),
    ],
)
def test_units_follow_the_locale_where_unset(name, units):
    assert units_for_locale(name) is units


def test_the_primary_location_is_the_first_one_and_a_given_one_wins(make_kernel):
    found = service(make_kernel, locations=["Lisbon, PT", "Berlin, DE"], units="metric")
    adapter = Fake()
    found.outlook(adapter, None, 3)
    found.outlook(adapter, "  Oslo ", 3)
    assert Fake.asked == ["Lisbon, PT", "Oslo"]


def test_days_stay_within_what_is_shown(make_kernel):
    found = service(make_kernel, locations=["x"], units="metric")
    assert len(found.outlook(Fake(), None, 0).days) == 1
    assert len(found.outlook(Fake(), None, 99).days) == 5  # the adapter gave five
    assert len(found.outlook(Fake(10), None, 99).days) == MAX_DAYS == 7


def test_no_location_says_how_to_set_one(make_kernel):
    with pytest.raises(HeinzelError, match=r"weather\.locations"):
        service(make_kernel).outlook(Fake(), None, 3)


def test_no_adapter_says_what_to_install(make_kernel):
    with pytest.raises(
        HeinzelError, match=r"No weather adapter is connected.*hz system config set capabilities.weather"
    ):
        service(make_kernel, bound=False, locations=["x"]).outlook(Fake(), None, 3)
    assert Fake.asked == []


def test_settings_default_to_no_location_and_the_locales_units():
    settings = WeatherSettings()
    assert (settings.locations, settings.units) == ([], None)


def test_units_default_to_the_locale_with_the_documented_precedence(make_kernel, monkeypatch):
    for name in ("LC_ALL", "LC_MEASUREMENT", "LANG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    monkeypatch.setenv("LC_MEASUREMENT", "en_US.UTF-8")
    assert service(make_kernel, locations=["x"]).units is Units.IMPERIAL
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    assert service(make_kernel, locations=["x"]).units is Units.METRIC
    assert service(make_kernel, locations=["x"], units="imperial").units is Units.IMPERIAL


def test_the_text_view_shows_units_rain_and_gaps():
    outlook = convert(report(2), Units.IMPERIAL, 2, "fake")
    console = Console(width=200, record=True, file=io.StringIO())
    console.print(render_outlook(outlook))
    text = console.export_text()
    assert "68°F" in text
    assert "1 in of rain in the last hour" in text
    assert "mph" in text
    assert "Next days" in text
    metric = convert(report(1), Units.METRIC, 1, "fake")
    console = Console(width=200, record=True, file=io.StringIO())
    console.print(render_outlook(metric))
    assert "25.4 mm of rain" in console.export_text()
