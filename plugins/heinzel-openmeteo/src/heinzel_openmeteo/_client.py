"""Open-Meteo over HTTPS with urllib: its geocoding and forecast APIs, free and keyless for non-commercial use.

Only this module sends requests, and only to the two hosts below; redirects are refused. Errors name the host, never
the URL, because the query holds the place the person asked about. Answers are checked against the models below
before anything uses them; fields Open-Meteo adds are ignored.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from heinzel import HeinzelError

FORECAST_HOST = "api.open-meteo.com"
GEOCODING_HOST = "geocoding-api.open-meteo.com"
HOSTS = (FORECAST_HOST, GEOCODING_HOST)
TIMEOUT = 10.0
"""Seconds to wait for Open-Meteo."""
MAX_BYTES = 1_000_000
"""The most an answer may hold; a forecast is a few kilobytes."""


class _Answer(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class Match(_Answer):
    """A geocoding match."""

    name: str
    latitude: float
    longitude: float
    country: str = ""
    country_code: str = ""
    admin1: str = ""


class Geocoding(_Answer):
    """The geocoding API's answer; it leaves `results` out when nothing matches."""

    results: list[Match] = []


class Current(_Answer):
    """The weather now, as the forecast API gives it (local time, no offset)."""

    time: str
    temperature_2m: float
    apparent_temperature: float
    relative_humidity_2m: int | None = None
    precipitation: float = Field(default=0.0, ge=0)
    weather_code: int
    wind_speed_10m: float = Field(default=0.0, ge=0)


class Daily(_Answer):
    """The daily forecast: one list per measure, by day."""

    time: list[str]
    weather_code: list[int | None]
    temperature_2m_max: list[float | None]
    temperature_2m_min: list[float | None]
    precipitation_sum: list[float | None] = []
    precipitation_probability_max: list[float | None] = []


class Forecast(_Answer):
    """The forecast API's answer, as far as the adapter uses it."""

    timezone: str
    current: Current
    daily: Daily


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: Any) -> None:  # noqa: ANN401 - urllib's signature
        return None


class Client:
    """Asks Open-Meteo; tests replace `get`, the one method that touches the network."""

    def get(self, host: str, path: str, params: dict[str, str]) -> Any:  # noqa: ANN401 - JSON, checked by the caller
        """The JSON `https://{host}{path}` answers for `params`.

        Raises:
            HeinzelError: If Open-Meteo can't be reached, refuses, or answers something that isn't JSON.
        """
        url = f"https://{host}{path}?{urllib.parse.urlencode(params)}"
        opener = urllib.request.build_opener(_NoRedirect)
        request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "heinzel"})
        failure = None
        body = b""
        try:
            with opener.open(request, timeout=TIMEOUT) as response:
                body = response.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as error:
            failure = f"answered {error.code}"
            error.close()
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            failure = f"can't be reached ({type(error).__name__})"
        if failure is not None:  # raised outside the except block, so no exception chain holds the URL
            msg = f"Open-Meteo ({host}) {failure}. Check your connection and try again; it is free but rate limited."
            raise HeinzelError(msg)
        if len(body) > MAX_BYTES:
            msg = f"Open-Meteo ({host}) answered more than {MAX_BYTES} bytes."
            raise HeinzelError(msg)
        try:
            return json.loads(body)
        except ValueError:
            msg = f"Open-Meteo ({host}) answered something that isn't JSON."
            raise HeinzelError(msg) from None

    def search(self, name: str) -> list[Match]:
        """Places called `name`, best match first (at most ten).

        Raises:
            HeinzelError: If Open-Meteo fails.
        """
        answer = self.get(
            GEOCODING_HOST, "/v1/search", {"name": name, "count": "10", "language": "en", "format": "json"}
        )
        return _parse(Geocoding, answer).results

    def forecast(self, latitude: float, longitude: float, days: int) -> Forecast:
        """The weather now and `days` days at a place, in metric units, in the place's own time zone.

        Raises:
            HeinzelError: If Open-Meteo fails or answers in a shape this plugin doesn't know.
        """
        params = {
            "latitude": f"{latitude:.4f}",
            "longitude": f"{longitude:.4f}",
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,"
            "wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
            "precipitation_probability_max",
            "timezone": "auto",
            "forecast_days": str(days),
            "wind_speed_unit": "kmh",
            "temperature_unit": "celsius",
            "precipitation_unit": "mm",
        }
        return _parse(Forecast, self.get(FORECAST_HOST, "/v1/forecast", params))


def _parse[M: BaseModel](model: type[M], data: object) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as error:
        fields = ", ".join(sorted({".".join(map(str, e["loc"])) for e in error.errors()})[:5])
        msg = f"Open-Meteo answered in a shape this plugin doesn't know (at {fields})."
        raise HeinzelError(msg) from None
