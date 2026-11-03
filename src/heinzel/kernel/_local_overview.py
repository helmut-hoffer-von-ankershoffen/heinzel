"""Local overview contracts and inert extension records (ADR 0119)."""

import os
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import JsonValue, TypeAdapter

from heinzel.kernel._lets import NAME_PATTERN, Let

ObservationState = Literal["not installed", "stopped", "running", "unhealthy", "unknown"]
STATES = frozenset({"not installed", "stopped", "running", "unhealthy", "unknown"})
MAX_RECORD_BYTES = 1024
MAX_RECORDS = 16


@dataclass(frozen=True, slots=True)
class LocalObservation:
    """A concise observation; historical evidence does not prove current readiness."""

    state: ObservationState
    detail: str = ""


@dataclass(frozen=True, slots=True)
class DaemonOverview:
    """Built-in scheduler observer; bound all local subprocesses by the monotonic deadline."""

    observe: Callable[[float], LocalObservation]


@dataclass(frozen=True, slots=True)
class WebOverview:
    """Built-in supervised web observer; absent on versions without supervised web services."""

    observe: Callable[[float], LocalObservation]


@dataclass(frozen=True, kw_only=True, slots=True)
class CachedOverviewLet(Let):
    """Declare historical sharing evidence without a callback, service factory, path or arbitrary display text.

    Add with `module.add(CachedOverviewLet(name="sharing"))`. The module owns a private atomic JSON record at
    `state_dir/overview/<module-qualified-name>.<name>.json`, containing exactly `state` (one of the five standard
    states) and `observed_at` (an aware ISO timestamp). No URL, credential or message is accepted or displayed.
    Records expire after five minutes; even a fresh record is explicitly historical and never current readiness.
    """

    def __post_init__(self) -> None:
        if not NAME_PATTERN.fullmatch(self.name):
            msg = "Overview names must be lowercase words joined by dashes."
            raise ValueError(msg)


def _private(info: os.stat_result, *, directory: bool) -> bool:
    kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    return kind and info.st_uid == os.getuid() and not info.st_mode & 0o077


def _read_record(state_dir: Path, filename: str) -> bytes | None:
    """Open only private physical directories and a private regular bounded file."""
    if not filename.endswith(".json") or any(
        not NAME_PATTERN.fullmatch(part) for part in filename.removesuffix(".json").split(".")
    ):
        return None
    parent = os.open(state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if not _private(os.fstat(parent), directory=True):
            return None
        directory = os.open("overview", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        if not _private(os.fstat(directory), directory=True):
            return None
        descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as source:
            if not _private(os.fstat(source.fileno()), directory=False):
                return None
            return source.read(MAX_RECORD_BYTES + 1)
    finally:
        os.close(directory)


def cached_observation(state_dir: Path, filename: str, deadline: float) -> LocalObservation:  # noqa: PLR0911
    """Read bounded private JSON directly; never execute plugin code or print arbitrary record fields."""
    unavailable = LocalObservation("unknown", "last observation unavailable")
    if time.monotonic() >= deadline:
        return unavailable
    try:
        data = _read_record(state_dir, filename)
        if data is None:
            return unavailable
        if len(data) > MAX_RECORD_BYTES:
            return unavailable
        record = TypeAdapter(dict[str, JsonValue]).validate_json(data)
        if set(record) != {"state", "observed_at"}:
            return unavailable
        state = record["state"]
        if not isinstance(state, str) or state not in STATES:
            return unavailable
        timestamp = record["observed_at"]
        if not isinstance(timestamp, str):
            return unavailable
        at = datetime.fromisoformat(timestamp)
        if at.tzinfo is None:
            return unavailable
        age = datetime.now(UTC) - at.astimezone(UTC)
        if age < timedelta(0) or age > timedelta(minutes=5) or time.monotonic() >= deadline:
            return LocalObservation("unknown", "last observation stale or unavailable")
        return LocalObservation("unknown", f"last observed {state} at {at.astimezone(UTC):%H:%M:%S UTC}")
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        return unavailable
