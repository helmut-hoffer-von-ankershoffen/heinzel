"""The optional source-aware extension to the calendar adapter contract."""

from typing import Protocol

from pydantic import BaseModel, Field

from heinzel.kernel._protocols import Event, Window


class CalendarSource(BaseModel):
    """A readable calendar exposed by a calendar adapter."""

    id: str = Field(min_length=1, max_length=1024, description="Stable opaque source id, suitable for events_for.")
    title: str = Field(min_length=1, max_length=256, description="Name of the calendar shown to its owner.")
    primary: bool = Field(default=False, description="Whether this is the owner's primary calendar.")


class CalendarSources(Protocol):
    """Optional source-aware reads alongside the calendar provider contract."""

    def calendars(self) -> list[CalendarSource]:
        """List readable calendars with stable ids. Mode: read."""
        ...

    def events_for(self, calendar: str, window: Window) -> list[Event]:
        """Read occurrences from the exact calendar id returned by calendars. Mode: read."""
        ...
