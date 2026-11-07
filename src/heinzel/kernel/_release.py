"""The latest release of Heinzelmännchen as last seen, cached so that no command waits for the network (ADR 0031).

Whoever asks the package source (`hz system release`, the CLI's hourly background check, later a daemon job)
remembers the answer here; heads read it to tell people that a newer release exists. The kernel knows neither the
package source, which a module provides as a `ReleaseSource`, nor how heads present it.
"""

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

CACHE_FILENAME = "release.json"


@dataclass(frozen=True)
class EdgeRelease:
    """The newest edge release (ADR 0114): an immutable wheel on GitHub, bound by its digest."""

    version: str
    wheel: str
    sha256: str


@dataclass(frozen=True)
class ReleaseFacts:
    """What automatic updates check about a release before installing it (ADR 0116)."""

    version: str
    published_at: datetime | None
    """When it was published; None when the source can't tell, which counts as too new."""
    commit: str | None = None
    """The commit its tag names, recorded when it is announced and checked once it is installed."""
    prerelease: bool = False


@dataclass(frozen=True)
class PluginRelease:
    """A newer stable, unyanked release of a plugin, with when it was uploaded (ADR 0116)."""

    version: str
    uploaded_at: datetime | None
    """When its last file was uploaded; None when the index can't tell, which counts as too new."""


class ReleaseSource:
    """Where the latest release of Heinzelmännchen comes from; the system module provides a subclass (ADR 0031).

    A base class rather than a protocol, so that the kernel can inject it by type like any other service.
    """

    def edge_release(self, timeout: float) -> EdgeRelease:
        """The newest edge release; asked only after someone chose the edge channel.

        Raises:
            NotImplementedError: Always; subclasses ask their package source.
        """
        raise NotImplementedError

    def latest_version(self, timeout: float) -> str:
        """The version of the latest release, waiting at most `timeout` seconds per network operation.

        Raises:
            NotImplementedError: Always; subclasses ask their package source and raise anything when it can't tell,
                which `ReleaseCache.refresh` records as a failure.
        """
        raise NotImplementedError

    def release_facts(self, version: str, timeout: float) -> ReleaseFacts:
        """When release `version` was published and which commit its tag names, for automatic updates.

        Raises:
            NotImplementedError: Always; subclasses ask their package source and raise anything when it can't tell.
        """
        raise NotImplementedError

    def highest_version(self, timeout: float, *, prereleases: bool = False) -> str:
        """The highest version among the published releases in SemVer order, for `hz system update`.

        Drafts never count; pre-releases only with `prereleases`. Waits at most `timeout` seconds per network
        operation.

        Raises:
            NotImplementedError: Always; subclasses ask their package source and raise anything when it can't tell.
        """
        raise NotImplementedError


class PluginReleases:
    """Newer releases of installed plugins; the system module provides a subclass (ADR 0116).

    A base class rather than a protocol, so that the kernel can inject it by type like `ReleaseSource`.
    """

    def newer(self, timeout: float) -> dict[str, str]:
        """Each installed plugin with a newer stable release, by distribution name, mapped to that version.

        Plugins whose source can't say (a git branch, a local folder) are left out. Waits at most `timeout` seconds
        per network operation and may raise anything when the source can't be asked.
        """
        del timeout
        return {}

    def release(self, name: str, version: str, timeout: float) -> PluginRelease | None:
        """Release `version` of distribution `name` on the package index with its last upload time (hz's own too).

        None when the index has no such unyanked release, which counts as too new; this base class never has one.
        """
        del name, version, timeout
        return None

    def newer_releases(self, timeout: float) -> dict[str, PluginRelease]:
        """Like `newer`, with when each release was uploaded; this base class can't tell, so none counts as old."""
        return {name: PluginRelease(version, None) for name, version in self.newer(timeout).items()}


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

    @property
    def error(self) -> str | None:
        """Why the package source couldn't be asked last time; None if it answered or was never asked."""
        error = self._read().get("error")
        return error if isinstance(error, str) else None

    @property
    def ok_at(self) -> datetime | None:
        """When the package source last answered; None if it never did (or only an older cache file exists)."""
        data = self._read()
        value = data.get("ok_at") or (data.get("checked_at") if "error" not in data else None)
        try:
            return datetime.fromisoformat(value) if isinstance(value, str) else None
        except ValueError:
            return None

    def is_fresh(self, max_age: timedelta, now: datetime | None = None) -> bool:
        """Whether the package source was asked, successfully or not, less than `max_age` ago.

        A time in the future (the clock went back) is not fresh, so a wrong clock can't stop the checks for good.
        """
        checked_at, now = self.checked_at, now or datetime.now(UTC)
        return checked_at is not None and now - max_age < checked_at <= now

    def newer_than(self, current: str, channel: str = "stable") -> str | None:
        """The latest release last seen if it is newer than `current`, else None.

        An edge version is only ever news on the edge channel: right after someone goes back to stable, the cache can
        still hold one (ADR 0114).
        """
        latest = self.latest
        try:
            if latest is None or (Version(latest).is_devrelease and channel != "edge"):
                return None
            return latest if Version(latest) > Version(current) else None
        except InvalidVersion:
            return None

    def remember(self, latest: str, checked_at: datetime | None = None) -> None:
        """Cache the latest release; an unwritable cache directory only costs the hint."""
        stamp = (checked_at or datetime.now(UTC)).isoformat()
        self._write({"latest": latest, "checked_at": stamp, "ok_at": stamp})

    def stamp(self, error: str | None = None, checked_at: datetime | None = None) -> None:
        """Record that the package source was asked, keeping the latest release last seen.

        Stamped before asking, so that an attempt that is cut short still counts; and after a failure, with its
        `error`, so that an offline machine waits for the next check instead of asking on every command.
        """
        data: dict[str, Any] = {"checked_at": (checked_at or datetime.now(UTC)).isoformat()}
        if (latest := self.latest) is not None:
            data["latest"] = latest
        if error is not None:
            data["error"] = error
        if (ok_at := self.ok_at) is not None:
            data["ok_at"] = ok_at.isoformat()  # a failed or cut-short attempt keeps the last answer's time
        self._write(data)

    def refresh(self, source: ReleaseSource, timeout: float) -> None:
        """Ask `source` for the latest release and remember it, or stamp the failure; never raises.

        Runs on a background thread in the CLI, where an exception would only print a traceback over the output.
        """
        try:
            latest = str(Version(source.latest_version(timeout)))
        except Exception as exc:  # noqa: BLE001 - isolation boundary: a failed check only costs the hint
            self.stamp(error=f"{type(exc).__name__}: {exc}")
            return
        self.remember(latest)

    def _write(self, data: dict[str, Any]) -> None:
        """Replace the cache file at once, so that a process ending mid-write never leaves half a file."""
        partial = self.path.with_name(f".{CACHE_FILENAME}.{os.getpid()}")
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            partial.write_text(json.dumps(data), encoding="utf-8")
            partial.replace(self.path)
        except OSError:
            return

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # missing or corrupt: a cache can always be lost
            return {}
        return data if isinstance(data, dict) else {}  # pyright: ignore[reportUnknownVariableType] - free-form JSON
