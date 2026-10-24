"""The latest release of Heinzelmännchen as last seen, cached so that no ordinary command needs the network (ADR 0031).

Whoever asks the package source (`hz system release`, later a daemon job) remembers the answer here; heads read it
to tell people that a newer release exists. The kernel knows neither the package source nor how heads present it.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

CACHE_FILENAME = "release.json"


@dataclass(frozen=True, slots=True)
class ReleaseCache:
    """The latest release last seen, in the cache directory (setting `cache_dir`); losing it only loses the hint."""

    cache_dir: Path

    @property
    def path(self) -> Path:
        """The cache file."""
        return self.cache_dir / CACHE_FILENAME

    @property
    def latest(self) -> str | None:
        """Version of the latest release last seen; None if nothing valid is cached."""
        latest = self._read().get("latest")
        if not isinstance(latest, str):
            return None
        try:
            return str(Version(latest))
        except InvalidVersion:
            return None

    @property
    def checked_at(self) -> datetime | None:
        """When the package source was last asked; None if unknown."""
        checked_at = self._read().get("checked_at")
        try:
            return datetime.fromisoformat(checked_at) if isinstance(checked_at, str) else None
        except ValueError:
            return None

    def newer_than(self, current: str) -> str | None:
        """The latest release last seen if it is newer than `current`, else None."""
        latest = self.latest
        try:
            return latest if latest is not None and Version(latest) > Version(current) else None
        except InvalidVersion:
            return None

    def remember(self, latest: str, checked_at: datetime | None = None) -> None:
        """Cache the latest release; an unwritable cache directory only costs the hint."""
        data = {"latest": latest, "checked_at": (checked_at or datetime.now(UTC)).isoformat()}
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            return

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # missing or corrupt: a cache can always be lost
            return {}
        return data if isinstance(data, dict) else {}  # pyright: ignore[reportUnknownVariableType] - free-form JSON
