"""The weather module: the weather where you are, from whichever adapter serves the `weather` capability.

`hz weather` shows the weather now and the next few days at your primary location, or at a place you name. The data
comes from the adapter bound to `weather` (ADR 0028): the first-party `openmeteo` plugin needs no account and no key;
a company's own plugin can serve the capability instead. Where you want the weather for is `[weather] locations`,
saved by the weather plugin's setup step (`hz setup openmeteo`) or `hz system config set`.
"""

from typing import Annotated

from pydantic import Field

from heinzel import Check, DetailLine, Module, Navigate, Status, StatusItem, WeatherSource
from heinzel.kernel import CapabilityView
from heinzel.modules.weather._models import Conditions, Outlook, OutlookDay, Units, WeatherSettings
from heinzel.modules.weather._render import render_outlook
from heinzel.modules.weather._service import DEFAULT_DAYS, MAX_DAYS, WeatherService

module = Module("weather", "The weather where you are, from whichever adapter serves the weather capability.")
module.settings(WeatherSettings)
module.service(WeatherService)

Location = Annotated[
    str | None,
    Field(
        description='A place such as "Lisbon, PT" or coordinates such as "38.72,-9.14"; your primary one if left out.'
    ),
]
MAX_TEXT = 80
"""The longest text of a status item (ADR 0056)."""
FIX_LOCATION = "hz system config set weather.locations '[\"Berlin, DE\"]'"
Days = Annotated[
    int, Field(ge=1, le=MAX_DAYS, description="How many days of forecast, from today; the adapter may give fewer.")
]


# Where you are is personal, and a web page can reach a local REST server: the CLI, MCP and the web head (which needs
# its session token) get it. Optional: without an adapter doctor stays green, and the command says what to install.
@module.operation(
    render=render_outlook, heads={"cli", "mcp", "web"}, requires=["weather:read"], optional=True, audience="everyday"
)
def weather(
    service: WeatherService, handle: WeatherSource, location: Location = None, days: Days = DEFAULT_DAYS
) -> Outlook:
    """Show the weather now and the next days at your primary location, or at a place you name.

    Raises:
        HeinzelError: If no weather adapter is connected, no location is set, the place is unknown, or the adapter
            fails.
    """
    return service.outlook(handle, location, days)


# The source of the status item: like `weather` but it asks for a location instead of failing while none is set.
@module.operation(heads={"cli", "mcp", "web"}, requires=["weather:read"], optional=True, audience="expert")
def conditions(service: WeatherService, handle: WeatherSource) -> Conditions:
    """Show the weather now at your primary location, or that a location is still to be set.

    Raises:
        HeinzelError: If no weather adapter is connected or the adapter fails.
    """
    return service.conditions(handle)


@module.status(source="conditions", order=800, refresh="10m", slots={"footer": True, "sidebar": True})
def weather_now(result: Conditions) -> StatusItem | None:
    """The temperature and sky now, linking to the weather page; "Set a location" until a place is named."""
    if result.needs_location:
        return StatusItem(text="Set a location", icon="cloud", tone="warn", effect=Navigate(path="/?settings=weather"))
    outlook = result.outlook
    if outlook is None:
        return None
    degree = outlook.units.degree
    text = f"{round(outlook.temperature) + 0}{degree} {outlook.summary}"
    return StatusItem(
        text=text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…",
        icon="cloud",
        detail=[
            DetailLine(text=outlook.location[:MAX_TEXT]),
            DetailLine(text=f"Feels like {round(outlook.feels_like) + 0}{degree}"),
        ],
        effect=Navigate(path="/weather"),
    )


@module.health
def locations(service: WeatherService, settings: WeatherSettings, view: CapabilityView) -> list[Check]:
    """A location is set once weather is in use; nothing is asked of any weather service."""
    if settings.locations:
        detail = f"{', '.join(settings.locations)} in {service.units.value} units"
        return [Check(name="locations", status=Status.UP, detail=detail)]
    if view.bound("weather") is None:
        return [Check(name="locations", status=Status.UP, detail="not used: no weather adapter is bound")]
    return [Check(name="locations", status=Status.DEGRADED, detail="none set", fix=FIX_LOCATION)]


__all__ = ["Conditions", "Outlook", "OutlookDay", "Units", "WeatherService", "WeatherSettings", "module"]
