"""Settings of the weather module and what `hz weather` returns, in the units you read."""

from datetime import date
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, Field

from heinzel import ModuleSettings


class Units(StrEnum):
    """How temperatures and wind speeds are shown."""

    METRIC = "metric"
    """Degrees Celsius, kilometres per hour, millimetres."""
    IMPERIAL = "imperial"
    """Degrees Fahrenheit, miles per hour, inches."""

    @property
    def degree(self) -> str:
        """The temperature unit's symbol."""
        return "°C" if self is Units.METRIC else "°F"

    @property
    def speed(self) -> str:
        """The wind speed unit."""
        return "km/h" if self is Units.METRIC else "mph"

    @property
    def length(self) -> str:
        """The unit of precipitation."""
        return "mm" if self is Units.METRIC else "in"


class WeatherSettings(ModuleSettings):
    """Where you want the weather for, and how: the `[weather]` table or `HZ_WEATHER__*` variables."""

    locations: list[str] = Field(
        default=[],
        description='Where you want the weather for, yours first (the primary one): a place such as "Berlin, DE" '
        'or coordinates such as "52.52,13.405".',
    )
    units: Units | None = Field(
        default=None,
        description="metric (°C, km/h) or imperial (°F, mph); unset: imperial where your locale is the US, "
        "Liberia or Myanmar, metric everywhere else.",
    )


class OutlookDay(BaseModel):
    """One day of the forecast, in the report's units."""

    day: date = Field(description="The day, in the place's time zone.")
    summary: str = Field(description="The sky in a few words.")
    high: float = Field(description="The day's high.")
    low: float = Field(description="The day's low.")
    rain_chance: float | None = Field(description="Chance of rain or snow, 0 to 1; none if the source has none.")
    precipitation: float = Field(description="Rain and snow expected over the day.")


class Outlook(BaseModel):
    """The weather now and the next days at one place, in the units you read."""

    location: str = Field(description="The place, as the weather adapter resolved it.")
    adapter: str = Field(description="The adapter bound to `weather` that answered.")
    units: Units = Field(description="metric or imperial: the unit of every number below.")
    observed_at: AwareDatetime = Field(description="When the weather now was observed, in the place's time zone.")
    summary: str = Field(description="The sky now in a few words.")
    temperature: float = Field(description="Air temperature now.")
    feels_like: float = Field(description="What it feels like now.")
    humidity_percent: int | None = Field(description="Relative humidity; none if the source has none.")
    wind: float = Field(description="Wind speed: km/h (metric) or mph (imperial).")
    precipitation: float = Field(description="Rain and snow in the last hour: mm (metric) or inches (imperial).")
    days: list[OutlookDay] = Field(description="The next days from today.")
