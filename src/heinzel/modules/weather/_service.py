"""The weather module's logic: which place, which units, and the report in them. Independent of any head."""

import locale
import os

from heinzel import DailyForecast, HeinzelError, WeatherReport, WeatherSource
from heinzel.kernel import CapabilityView, Requirement
from heinzel.modules.weather._models import Outlook, OutlookDay, Units, WeatherSettings

WEATHER = Requirement.parse("weather:read")
IMPERIAL_REGIONS = frozenset({"US", "LR", "MM"})
"""Where Fahrenheit and miles per hour are what people read: the United States, Liberia, Myanmar."""
DEFAULT_DAYS = 3
"""How many days a report shows unless asked for more."""
MAX_DAYS = 7
"""The most days a report shows; an adapter may forecast fewer."""
NO_LOCATION = (
    "No location is set. Name one with `hz weather --location 'Lisbon, PT'`, or save yours: "
    "`hz system config set weather.locations '[\"Berlin, DE\"]'`."
)
_KM_PER_MILE = 1.609344
_MM_PER_INCH = 25.4


def units_for_locale(name: str | None = None) -> Units:
    """Imperial where the locale's region is the US, Liberia or Myanmar; metric everywhere else.

    `name` is a locale such as `en_US.UTF-8`; by default the environment's (`LC_ALL`, `LC_MEASUREMENT`, `LANG`),
    then Python's.
    """
    if name is None:
        name = next(
            (value for key in ("LC_ALL", "LC_MEASUREMENT", "LANG") if (value := os.environ.get(key))), None
        ) or (locale.getlocale()[0] or "")
    region = name.split(".")[0].replace("-", "_").partition("_")[2].upper()
    return Units.IMPERIAL if region in IMPERIAL_REGIONS else Units.METRIC


class WeatherService:
    """Reads the weather through the bound adapter and shows it in your units."""

    def __init__(self, settings: WeatherSettings, view: CapabilityView) -> None:
        """Create the service; the module's settings and the capability view are injected."""
        self._settings = settings
        self._view = view

    @property
    def units(self) -> Units:
        """The configured units, or those of your locale."""
        return self._settings.units or units_for_locale()

    def primary(self) -> str:
        """The primary location: the first configured one.

        Raises:
            HeinzelError: If none is configured.
        """
        if not self._settings.locations:
            raise HeinzelError(NO_LOCATION)
        return self._settings.locations[0]

    def outlook(self, weather: WeatherSource, location: str | None, days: int) -> Outlook:
        """The weather now and `days` days at `location` or the primary one, in your units.

        Raises:
            HeinzelError: If no weather adapter serves it, no location is set, or the adapter fails.
        """
        adapter, problem = self._view.serves(WEATHER)
        if adapter is None or problem is not None:
            msg = f"No weather adapter is connected ({WEATHER}: {problem}). {self._view.fix('weather')}"
            raise HeinzelError(msg)
        query = (location or "").strip() or self.primary()
        return convert(weather.report(query), self.units, min(max(days, 1), MAX_DAYS), adapter)


def convert(report: WeatherReport, units: Units, days: int, adapter: str) -> Outlook:
    """`report`, which is metric, in `units`, limited to `days` days."""
    now = report.current
    return Outlook(
        location=report.place.name,
        adapter=adapter,
        units=units,
        observed_at=now.observed_at,
        summary=now.summary,
        temperature=_degrees(now.temperature_c, units),
        feels_like=_degrees(now.feels_like_c, units),
        humidity_percent=now.humidity_percent,
        wind=_speed(now.wind_kmh, units),
        precipitation=_length(now.precipitation_mm, units),
        days=[_day(day, units) for day in report.days[:days]],
    )


def _day(day: DailyForecast, units: Units) -> OutlookDay:
    return OutlookDay(
        day=day.day,
        summary=day.summary,
        high=_degrees(day.high_c, units),
        low=_degrees(day.low_c, units),
        rain_chance=day.rain_chance,
        precipitation=_length(day.precipitation_mm, units),
    )


def _degrees(celsius: float, units: Units) -> float:
    return round(celsius if units is Units.METRIC else celsius * 9 / 5 + 32, 1)


def _speed(kmh: float, units: Units) -> float:
    return round(kmh if units is Units.METRIC else kmh / _KM_PER_MILE, 1)


def _length(mm: float, units: Units) -> float:
    return round(mm if units is Units.METRIC else mm / _MM_PER_INCH, 2)
