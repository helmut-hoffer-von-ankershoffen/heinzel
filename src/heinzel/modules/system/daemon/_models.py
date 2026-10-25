"""Models of the daemon module: settings, receipts and what its operations return."""

import sys
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field, JsonValue

from heinzel import CatchUp, ModuleSettings


def unit_dir(platform: str) -> Path:
    """Where launchd (macOS) or systemd (Linux) looks for a user's units."""
    if platform == "darwin":
        return Path("~/Library/LaunchAgents").expanduser()
    return Path("~/.config/systemd/user").expanduser()


class DaemonSettings(ModuleSettings):
    """Settings of the daemon module: the `[system.daemon]` table or `HZ_SYSTEM_DAEMON__*` variables."""

    section_title: ClassVar[str | None] = "Daemon"  # the heading in Settings

    unit_dir: Path = Field(
        default_factory=lambda: unit_dir(sys.platform),
        title="Service folder",
        description="Where installing the daemon writes its LaunchAgent on macOS, or its systemd user units on Linux.",
        json_schema_extra={"x-expert": True},
    )


class Wake(StrEnum):
    """Why a job ran."""

    SCHEDULE = "schedule"
    MANUAL = "manual"


class Disposition(StrEnum):
    """How a run ended."""

    RENDERED = "rendered"
    SKIPPED = "skipped"
    FAILED = "failed"


class Receipt(BaseModel):
    """The record of one run of a job; hash-chained per job and never edited (ADR 0020)."""

    id: str = Field(description="SHA-256 of the receipt's canonical JSON without the id.")
    prev: str | None = Field(description="Id of the job's previous receipt; None for its first.")
    job: str = Field(description="The job, as `<module>:<job>`.")
    wake: Wake = Field(description="Why it ran: its schedule, or by hand with `tick --job`.")
    disposition: Disposition = Field(description="rendered, skipped or failed.")
    as_of: datetime = Field(description="The schedule slot the run covers; the time of a manual run.")
    idempotency_key: str = Field(description="Key of the run; a slot whose key was rendered never runs again.")
    error: str | None = Field(default=None, description="What went wrong, when failed; why, when skipped.")
    result: JsonValue = Field(default=None, description="What the job returned, as JSON, when rendered.")


class JobRun(BaseModel):
    """What one tick did, or would do, with one job."""

    job: str = Field(description="The job, as `<module>:<job>`.")
    as_of: datetime = Field(description="The schedule slot, or the time of a manual run.")
    wake: Wake = Field(description="Why it ran.")
    disposition: Disposition | None = Field(description="How it ended; None in a dry run.")
    error: str | None = Field(default=None, description="What went wrong, or why it was skipped.")
    receipt: str | None = Field(default=None, description="Id of the receipt written; None in a dry run.")


class TickReport(BaseModel):
    """The outcome of one tick."""

    at: datetime = Field(description="When the tick started.")
    dry_run: bool = Field(description="Whether jobs were only computed, not run.")
    paused: bool = Field(description="Whether the daemon is paused, so nothing ran.")
    locked: bool = Field(description="Whether another tick held the lock, so nothing ran.")
    runs: list[JobRun] = Field(description="The jobs that were due, or asked for, and what became of them.")


class JobInfo(BaseModel):
    """A job as `hz system daemon jobs` lists it."""

    job: str = Field(description="The job, as `<module>:<job>`; pass it to `tick --job`.")
    summary: str = Field(description="What the job does.")
    schedule: str = Field(description="Its cron expression, in local time.")
    scope: str = Field(description="Who runs it: personal.")
    timeout_seconds: float = Field(description="How long one run may take.")
    retries: int = Field(description="Retries within a tick after a failure.")
    catch_up: CatchUp = Field(description="What happens to missed slots: latest, all or skip.")
    next_run: datetime | None = Field(description="The next slot; None if the schedule is invalid.")
    last_disposition: Disposition | None = Field(description="How its last run ended; None if it never ran.")
    last_as_of: datetime | None = Field(description="The slot or time of its last run.")
    problem: str | None = Field(default=None, description="Why the job can't run, e.g. an invalid schedule.")


class Installed(BaseModel):
    """What `install` wrote, or would write, and how it was loaded."""

    platform: str = Field(description="darwin (launchd) or linux (systemd).")
    executable: Path = Field(description="The stable uv tool shim the unit runs.")
    interval_minutes: int = Field(description="Minutes between ticks.")
    files: dict[Path, str] = Field(description="Unit files and their content.")
    commands: list[list[str]] = Field(description="Commands run to load the unit; none in a dry run.")
    dry_run: bool = Field(description="Whether nothing was written or loaded.")
    hint: str | None = Field(default=None, description="What to do next, if anything.")


class Uninstalled(BaseModel):
    """What `uninstall` removed."""

    removed: list[Path] = Field(description="Unit files removed.")
    commands: list[list[str]] = Field(description="Commands run to unload the unit.")


class DaemonStatus(BaseModel):
    """Whether the daemon is installed, loaded and healthy."""

    platform: str = Field(description="The platform's service manager: darwin (launchd) or linux (systemd).")
    installed: bool = Field(description="Whether the unit files exist.")
    loaded: bool = Field(description="Whether launchd or systemd has the unit loaded.")
    paused: bool = Field(description="Whether ticks are paused with `hz system daemon pause`.")
    interval_minutes: int | None = Field(description="Minutes between ticks, as installed.")
    executable: Path | None = Field(description="The shim the unit runs, as installed.")
    last_tick: datetime | None = Field(description="When the last tick ran.")
    jobs: int = Field(description="How many jobs are registered.")
    failing: list[str] = Field(description="Jobs whose last run failed or that can't run.")


class Toggled(BaseModel):
    """The pause flag after `pause` or `resume`."""

    paused: bool = Field(description="Whether ticks are now paused.")


class LogLine(BaseModel):
    """One line of the daemon's JSON-lines log."""

    at: datetime = Field(description="When it was written.")
    level: str = Field(description="INFO, WARNING or ERROR.")
    message: str = Field(description="What happened.")
    tick: str | None = Field(default=None, description="The tick it belongs to.")
    job: str | None = Field(default=None, description="The job it concerns.")
    receipt: str | None = Field(default=None, description="The receipt it wrote.")
