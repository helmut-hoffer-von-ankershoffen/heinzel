"""Settings of the directory module."""

from typing import ClassVar

from pydantic import Field

from heinzel import ModuleSettings


class DirectorySettings(ModuleSettings):
    """How the `one-on-ones` adapter guesses: the `[system.directory]` table or `HZ_SYSTEM_DIRECTORY__*` variables."""

    section_title: ClassVar[str | None] = "Reporting lines"  # the heading in Settings

    lookback_days: int = Field(
        default=56,
        ge=42,
        le=365,
        title="Days to look back",
        description="How many days of your calendar, up to today, to search for recurring "
        "one-on-ones; at least 42, so a meeting every two weeks shows up three times.",
        json_schema_extra={"x-expert": True, "x-unit": "days"},
    )
    min_occurrences: int = Field(
        default=3,
        ge=2,
        le=52,
        title="Meetings before a guess",
        description="How often two people must have met alone, weekly or every two weeks, before "
        "a reporting line between them is guessed.",
        json_schema_extra={"x-expert": True},
    )
