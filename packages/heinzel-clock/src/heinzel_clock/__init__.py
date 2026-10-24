"""What time it is for everyone on the team.

The whole plugin: one module, its settings, one operation and a health check. Heinzelmännchen provides the CLI
command, MCP tool, REST endpoint, configuration, validation and health reporting.
"""

from datetime import UTC, datetime
from typing import Annotated
from zoneinfo import ZoneInfo, available_timezones

from pydantic import BaseModel, Field

from heinzel import Check, HeinzelError, Module, ModuleSettings, Status

module = Module("clock", "What time it is for everyone on the team.")


@module.settings
class ClockSettings(ModuleSettings):
    """Where the team is."""

    zones: list[str] = Field(
        default=["Europe/Berlin", "America/New_York", "America/Los_Angeles"],
        description="The team's time zones, as IANA names.",
    )
    working_hours: tuple[int, int] = Field(default=(9, 18), description="Start and end hour of a working day.")


class ZoneTime(BaseModel):
    """The time in one zone."""

    zone: str = Field(description="IANA time zone name, e.g. Europe/Berlin.")
    time: str = Field(description="Local weekday and time there, e.g. Mon 14:05.")
    working: bool = Field(description="Whether it is within working hours there.")


@module.operation
def clock(
    settings: ClockSettings,
    at: Annotated[datetime | None, Field(description="Moment to show instead of now (ISO 8601).")] = None,
) -> list[ZoneTime]:
    """Show the time in each of the team's time zones.

    Raises:
        HeinzelError: If a configured zone is unknown.
    """
    if unknown := [zone for zone in settings.zones if zone not in available_timezones()]:
        msg = f"Unknown time zones: {', '.join(unknown)}. Run `hz system doctor` for the fix."
        raise HeinzelError(msg)
    moment = at.astimezone(UTC) if at else datetime.now(UTC)
    start, end = settings.working_hours
    return [
        ZoneTime(zone=zone, time=local.strftime("%a %H:%M"), working=start <= local.hour < end)
        for zone in settings.zones
        for local in [moment.astimezone(ZoneInfo(zone))]
    ]


@module.health
def zones(settings: ClockSettings) -> list[Check]:
    """Every configured zone must be a known IANA time zone."""
    unknown = [zone for zone in settings.zones if zone not in available_timezones()]
    if unknown:
        fix = "hz system config set clock.zones '[\"Europe/Berlin\"]'"
        return [Check(name="zones", status=Status.DOWN, detail=f"unknown: {', '.join(unknown)}", fix=fix)]
    return [Check(name="zones", status=Status.UP, detail=", ".join(settings.zones))]
