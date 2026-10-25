"""The Open-Meteo adapter of the `weather` capability (ADR 0028), read mode only.

A place is `lat,lon` or a name; a name is geocoded with Open-Meteo's free geocoding API, optionally narrowed by what
follows a comma (`Lisbon, PT`, `Portland, Oregon`): a country code, a country or a region. Everything is metric.
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from heinzel import (
    CurrentWeather,
    DailyForecast,
    HeinzelError,
    WeatherPlace,
    WeatherReport,
    clean_text,
)
from heinzel_openmeteo._client import Client, Match
from heinzel_openmeteo._codes import describe

DAYS = 7
"""How many days a report holds, as many as `hz weather --days` allows; Open-Meteo forecasts up to sixteen."""
COORDINATES = re.compile(r"\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*")
"""A place given as `lat,lon`."""


class OpenMeteo:
    """The weather at a place, from Open-Meteo, which needs no account and no key."""

    def __init__(self, client: Client) -> None:
        """Build the adapter; the kernel injects the client."""
        self._client = client

    def report(self, place: str) -> WeatherReport:
        """The weather now and the next seven days at `place`: a name to geocode, or `lat,lon`. Mode: read.

        Raises:
            HeinzelError: If the place is empty or unknown, or Open-Meteo fails.
        """
        found = self._locate(place)
        answer = self._client.forecast(found.latitude, found.longitude, DAYS)
        try:
            zone = ZoneInfo(answer.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            msg = f"Open-Meteo named a time zone this machine doesn't know: {clean_text(answer.timezone)[:40]!r}."
            raise HeinzelError(msg) from None
        now = answer.current
        daily = answer.daily
        try:
            observed = datetime.fromisoformat(now.time).replace(tzinfo=zone)
            dates = [datetime.fromisoformat(day).date() for day in daily.time]
        except ValueError:
            msg = "Open-Meteo answered a date this plugin doesn't know."
            raise HeinzelError(msg) from None
        days = [
            DailyForecast(
                day=dates[index],
                summary=describe(_at(daily.weather_code, index)),
                high_c=high,
                low_c=low,
                rain_chance=None if chance is None else min(max(chance / 100, 0), 1),
                precipitation_mm=max(_at(daily.precipitation_sum, index) or 0.0, 0.0),
            )
            for index in range(len(dates))
            if (high := _at(daily.temperature_2m_max, index)) is not None
            and (low := _at(daily.temperature_2m_min, index)) is not None
            for chance in [_at(daily.precipitation_probability_max, index)]
        ]
        return WeatherReport(
            place=found,
            current=CurrentWeather(
                observed_at=observed,
                summary=describe(now.weather_code),
                temperature_c=now.temperature_2m,
                feels_like_c=now.apparent_temperature,
                humidity_percent=now.relative_humidity_2m,
                wind_kmh=now.wind_speed_10m,
                precipitation_mm=now.precipitation,
            ),
            days=days,
        )

    def _locate(self, place: str) -> WeatherPlace:
        text = clean_text(place).strip()
        if not text:
            msg = "Name a place such as 'Lisbon, PT', or give coordinates such as '38.72,-9.14'."
            raise HeinzelError(msg)
        if match := COORDINATES.fullmatch(text):
            lat, lon = float(match[1]), float(match[2])
            if -90 <= lat <= 90 and -180 <= lon <= 180:  # noqa: PLR2004 - degrees
                return WeatherPlace(name=f"{lat:g}, {lon:g}", latitude=lat, longitude=lon)
        name, _, qualifier = (part.strip() for part in text.partition(","))
        matches = self._client.search(name) if name else []
        chosen = _choose(matches, qualifier)
        if chosen is None:
            msg = f"Open-Meteo knows no place called {text!r}; try a city and a country code, such as 'Berlin, DE'."
            raise HeinzelError(msg)
        label = ", ".join(clean_text(part) for part in (chosen.name, chosen.admin1, chosen.country) if part)
        return WeatherPlace(name=label, latitude=chosen.latitude, longitude=chosen.longitude)


def _choose(matches: list[Match], qualifier: str) -> Match | None:
    if not qualifier:
        return matches[0] if matches else None
    wanted = qualifier.casefold()
    return next(
        (m for m in matches if wanted in {m.country_code.casefold(), m.country.casefold(), m.admin1.casefold()}), None
    )


def _at[T](values: list[T | None], index: int) -> T | None:
    return values[index] if index < len(values) else None
