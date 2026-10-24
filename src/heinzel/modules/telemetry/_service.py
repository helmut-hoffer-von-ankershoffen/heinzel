"""Append-only logs, one per Heinzelmännchen, and their copies in the team repository (ADR 0053).

A log is `<state dir>/telemetry/<heinzel_id>.jsonl`, one JSON entry per line. Only `hz report` writes it, appending
under an exclusive lock so a daemon and a handoff reporting at once don't interleave, and never through a symbolic
link. `sync` copies each log to `telemetry/<user>/<heinzel_id>.jsonl` in the team repository's checkout; a copy must be
a prefix of the log it comes from, so a file in the team repository has one writer, the user it is filed under.
Reading the team's copies is reading someone else's data: every line is validated again, and a line filed under
another user or Heinzelmännchen than its own is skipped and named.
"""

import fcntl
import os
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from heinzel import Config, HeinzelError, Settings, TelemetryEntry, TelemetryLog, TelemetryRead
from heinzel.kernel import HEINZEL_ID, KERNEL_SECTION, Kernel, OrgContext
from heinzel.modules.telemetry._models import HANDLE_PATTERN, Reported, Synced, SyncedLog, TelemetrySettings

DIRECTORY = "telemetry"
SUFFIX = ".jsonl"
MAX_LINE = 4096
"""Bytes one entry may take; an entry is metadata, a few hundred bytes."""
MAX_LOG = 8 * 1024 * 1024
"""Bytes a log may grow to before hz refuses to read or append it: years of entries, never a dump."""
MAX_SKEW = timedelta(minutes=5)
MAX_AGE = timedelta(days=30)
"""How far back an entry may be dated: a late report of a handoff's publish, not a rewritten history."""
_HANDLE = re.compile(HANDLE_PATTERN)


def _open_flags(base: int) -> int:
    return base | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


class TelemetryService:
    """Appends to the local logs, reads them and the team's copies, and copies them into the team's checkout."""

    def __init__(
        self,
        kernel: Kernel,
        settings: TelemetrySettings,
        state_dir: Path,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Keep logs below `state_dir`; tests pass the clock, `for_user` builds it for the kernel."""
        self._kernel = kernel
        self._settings = settings
        self.directory = state_dir / DIRECTORY
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def for_user(cls, kernel: Kernel, settings: TelemetrySettings) -> "TelemetryService":
        """The service with user-scope kernel settings: a project's `hz.toml` can't move the logs."""
        config = Config.load(Path.home())
        return cls(kernel, settings, config.settings(Settings, KERNEL_SECTION).state_dir)

    @property
    def checkout(self) -> Path | None:
        """Your team repository's checkout, from the `OrgContext`; None without a team or the team module."""
        if not self._kernel.provides(OrgContext):
            return None
        return self._kernel.resolve(OrgContext).team_checkout()

    # Writing ---------------------------------------------------------------------------------------------------------

    def report(self, fields: dict[str, object], *, user: str | None, at: datetime | None) -> Reported:
        """Validate one entry and append it to its Heinzelmännchen's log.

        Raises:
            HeinzelError: If the entry isn't valid, is dated in the future or too long ago, or the log can't be written.
        """
        now = self._clock()
        when = at or now
        if when.tzinfo is None:
            msg = f"--at {when.isoformat()} needs an offset, e.g. {now.isoformat(timespec='minutes')}"
            raise HeinzelError(msg)
        if when > now + MAX_SKEW or when < now - MAX_AGE:
            msg = f"--at {when.isoformat()} must be within the last {MAX_AGE.days} days, never in the future"
            raise HeinzelError(msg)
        try:
            entry = TelemetryEntry.model_validate({**fields, "user": self._settings.handle(user), "time": when})
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'entry'}: {e['msg'].removeprefix('Value error, ')}"
                for e in exc.errors()
            )
            msg = f"Not reported: {problems}"
            raise HeinzelError(msg) from exc
        log = self.directory / f"{entry.heinzel_id}{SUFFIX}"
        line = entry.model_dump_json(exclude_none=True, exclude_defaults=False) + "\n"
        count = self._append(log, entry, line.encode())
        return Reported(entry=entry, log=log, entries=count, next=self._next_after_report())

    def _next_after_report(self) -> list[str]:
        if self.checkout is None:
            return ["join your team to share it: hz team join <url>"]
        return ["copy your logs into the team repository: hz report sync"]

    def _append(self, log: Path, entry: TelemetryEntry, line: bytes) -> int:
        """Append `line` under an exclusive lock, refusing a log of another user; the count of entries after it.

        Raises:
            HeinzelError: If the log is a link, too large, another user's, or can't be written.
        """
        if self.directory.is_symlink():
            msg = f"{self.directory} is a symbolic link; hz writes logs only into a real directory"
            raise HeinzelError(msg)
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(log, _open_flags(os.O_RDWR | os.O_CREAT | os.O_APPEND), 0o600)
        except OSError as exc:
            msg = f"Can't write {log}: {exc.strerror or exc}"
            raise HeinzelError(msg) from exc
        with os.fdopen(descriptor, "r+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            existing = handle.read(MAX_LOG + 1)
            if len(existing) + len(line) > MAX_LOG:
                msg = f"{log} is over {MAX_LOG // 1024 // 1024} MiB; a log holds metadata, so something is wrong"
                raise HeinzelError(msg)
            lines = existing.splitlines()
            if lines and (owner := _user_of(lines[0])) not in {None, entry.user}:
                msg = (
                    f"{log} holds the entries of {owner}, not {entry.user}: one writer per log; "
                    f"report as {owner}, or give this Heinzelmännchen another id"
                )
                raise HeinzelError(msg)
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return len(lines) + 1

    # Reading ---------------------------------------------------------------------------------------------------------

    def _local_logs(self) -> list[Path]:
        if not self.directory.is_dir() or self.directory.is_symlink():
            return []
        return sorted(p for p in self.directory.glob(f"*{SUFFIX}") if p.is_file() and not p.is_symlink())

    def read(self) -> TelemetryRead:
        """The team's entries: its repository's copies and your own logs, yours winning for your Heinzelmännchen."""
        logs: dict[tuple[str, str], list[TelemetryEntry]] = {}
        problems: list[str] = []
        if (checkout := self.checkout) is not None:
            for path, user in self._team_logs(checkout / DIRECTORY, problems):
                entries = list(
                    self._entries(path, problems, user=user, heinzel=path.stem, shown=path.relative_to(checkout))
                )
                logs[user, path.stem] = entries
        for path in self._local_logs():
            entries = list(self._entries(path, problems, user=None, heinzel=path.stem, shown=path))
            if entries:
                logs[entries[0].user, path.stem] = entries
        everything = sorted((e for entries in logs.values() for e in entries), key=lambda e: (e.time, e.heinzel_id))
        return TelemetryRead(entries=tuple(everything), problems=tuple(problems))

    @staticmethod
    def _team_logs(root: Path, problems: list[str]) -> Iterator[tuple[Path, str]]:
        if not root.is_dir() or root.is_symlink():
            return
        for folder in sorted(root.iterdir()):
            if folder.is_symlink() or not folder.is_dir():
                continue
            if not _HANDLE.fullmatch(folder.name):
                problems.append(f"{DIRECTORY}/{folder.name}: not a roster handle, skipped")
                continue
            for path in sorted(folder.glob(f"*{SUFFIX}")):
                if path.is_symlink() or not path.is_file() or not HEINZEL_ID.fullmatch(path.stem):
                    problems.append(f"{DIRECTORY}/{folder.name}/{path.name}: not a Heinzelmännchen's log, skipped")
                    continue
                yield path, folder.name

    @staticmethod
    def _entries(
        path: Path, problems: list[str], *, user: str | None, heinzel: str, shown: Path
    ) -> Iterator[TelemetryEntry]:
        try:
            if path.stat().st_size > MAX_LOG:
                problems.append(f"{shown}: over {MAX_LOG // 1024 // 1024} MiB, skipped")
                return
            raw = path.read_bytes()
        except OSError as exc:
            problems.append(f"{shown}: can't be read: {exc.strerror or exc}")
            return
        first: str | None = user
        for number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            if len(line) > MAX_LINE:
                problems.append(f"{shown}:{number}: longer than {MAX_LINE} bytes, skipped")
                continue
            try:
                entry = TelemetryEntry.model_validate_json(line)
            except ValidationError as exc:
                problems.append(f"{shown}:{number}: {exc.errors()[0]['msg'].removeprefix('Value error, ')}, skipped")
                continue
            first = first or entry.user
            if entry.heinzel_id != heinzel or entry.user != first:
                problems.append(f"{shown}:{number}: an entry of {entry.user}/{entry.heinzel_id} filed here, skipped")
                continue
            yield entry

    # Syncing ---------------------------------------------------------------------------------------------------------

    def sync(self) -> Synced:
        """Copy every local log into the team repository's checkout, each a prefix-extension of the copy there.

        Raises:
            HeinzelError: Without a team repository.
        """
        checkout = self.checkout
        if checkout is None:
            msg = "You have no team repository to copy your logs into; join one: hz team join <url>"
            raise HeinzelError(msg)
        synced = Synced(checkout=checkout)
        for log in self._local_logs():
            copied = self._sync_one(log, checkout)
            if isinstance(copied, str):
                synced.problems.append(f"{log.name}: {copied}")
            else:
                synced.logs.append(copied)
        if synced.logs:
            synced.next.append(
                f"commit and push them, in a pull request if your team wants one: git -C {checkout} add {DIRECTORY} "
                f'&& git -C {checkout} commit -m "chore(telemetry): sync" && git -C {checkout} push'
            )
        return synced

    def _sync_one(self, log: Path, checkout: Path) -> SyncedLog | str:
        """Copy one log into the checkout; or why it can't be."""
        found: list[str] = []
        entries = list(self._entries(log, found, user=None, heinzel=log.stem, shown=log))
        if found or not entries:
            return found[0] if found else "empty"
        folder = checkout / DIRECTORY / entries[0].user
        target = folder / log.name
        if link := next((p for p in (checkout / DIRECTORY, folder, target) if p.is_symlink()), None):
            return f"{link} is a symbolic link; hz copies logs only into real directories"
        try:
            raw = log.read_bytes()
            before = target.read_bytes() if target.exists() else b""
            if not raw.startswith(before):
                return (
                    f"{target} has entries your log doesn't: each log has one writer; move that copy aside if it "
                    "isn't yours, or keep reporting from the machine that wrote it"
                )
            folder.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(raw)
            temporary.replace(target)
        except OSError as exc:
            return f"can't copy it to {target}: {exc.strerror or exc}"
        new = len(raw.splitlines()) - len(before.splitlines())
        return SyncedLog(heinzel_id=log.stem, path=target, entries=len(raw.splitlines()), new=new)


def _user_of(line: bytes) -> str | None:
    try:
        return TelemetryEntry.model_validate_json(line).user
    except ValidationError:
        return None


class TeamTelemetry(TelemetryLog):
    """The `TelemetryLog` the kernel injects: the team's copies and your own logs, read afresh on every call."""

    def __init__(self, service: TelemetryService) -> None:
        """Read through `service`."""
        self._service = service

    def entries(self) -> TelemetryRead:
        """The team's entries, oldest first, and what was skipped."""
        return self._service.read()
