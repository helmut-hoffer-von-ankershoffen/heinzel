"""Settings of the directory module."""

from pydantic import Field

from heinzel import ModuleSettings


class DirectorySettings(ModuleSettings):
    """How the `one-on-ones` adapter guesses: the `[system.directory]` table or `HZ_SYSTEM_DIRECTORY__*` variables."""

    lookback_days: int = Field(
        default=56,
        ge=14,
        le=365,
        description="How many days of the bound calendar, up to now, to look for recurring 1:1s in.",
    )
    min_occurrences: int = Field(
        default=3,
        ge=2,
        le=52,
        description="How often two people must have met alone, weekly or every two weeks, before hz guesses a "
        "reporting line between them.",
    )
