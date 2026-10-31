"""Reviewed quiet hours of a schedule: during them a due slot starts no model turn and sends nothing."""

import re
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_CLOCK = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


def _clock(value: str) -> time:
    return time(int(value[:2]), int(value[3:]))


class QuietHours(BaseModel):
    """A daily local-time window; a window that ends earlier than it starts runs over midnight."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    start: str = Field(description="Local time the quiet window begins, as HH:MM; inclusive.")
    end: str = Field(description="Local time the quiet window ends, as HH:MM; exclusive.")
    timezone: str = Field(min_length=1, max_length=128, description="IANA timezone of the window.")

    @field_validator("start", "end")
    @classmethod
    def valid_clock(cls, value: str) -> str:
        """Require a 24-hour clock time.

        Raises:
            ValueError: If the value is not HH:MM.
        """
        if not _CLOCK.fullmatch(value):
            msg = "Quiet hours use a 24-hour HH:MM time"
            raise ValueError(msg)
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        """Require a real IANA timezone rather than guessing one.

        Raises:
            ValueError: If the timezone is unknown.
        """
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            msg = "The quiet hours timezone is invalid"
            raise ValueError(msg) from None
        return value

    @model_validator(mode="after")
    def not_empty(self) -> "QuietHours":
        """Reject a window that says nothing.

        Raises:
            ValueError: If start and end are the same time.
        """
        if self.start == self.end:
            msg = "Quiet hours must start and end at different times"
            raise ValueError(msg)
        return self

    def covers(self, when: datetime) -> bool:
        """Whether this aware instant falls in the window, in the window's own timezone."""
        local = when.astimezone(ZoneInfo(self.timezone)).time().replace(second=0, microsecond=0)
        start, end = _clock(self.start), _clock(self.end)
        return start <= local < end if start < end else local >= start or local < end
