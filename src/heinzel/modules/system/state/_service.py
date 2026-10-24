"""The state module's service: note presence and adoption locally, push them to your personal state repository.

Writing is local and always on: a line a day, and a line per version, in your state directory. Nothing leaves the
machine until you name your own private repository in `[system.state] personal`; then the daemon's `push` job, every
15 minutes with up to 90 seconds of jitter, copies what is new to this machine's writer branch (ADR 0062). A failed
push backs off, doubling up to a day; a branch that holds what this machine didn't write stops pushing until
`hz system state reset`. The service reads user-scope configuration only, so a project's `hz.toml` can't redirect it.
"""

import hashlib
import json
import os
import secrets
import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue

from heinzel import Check, Config, HeinzelError, Settings, Status, __version__
from heinzel.kernel import KERNEL_SECTION, GitRunner, Presence, RunCommand
from heinzel.modules.system.state._models import (
    STREAMS,
    Os,
    Pushed,
    ReportUser,
    StateSettings,
    StateStatus,
    StreamStatus,
)
from heinzel.modules.system.state._sink import PushFailed, Refused, Sink, scrub
from heinzel.modules.system.state._streams import Segment, Streams, problems

MODULE_SECTION = "system.state"
PUSH_FILE = "push.json"
EVERY = timedelta(minutes=15)
"""How often the daemon's job pushes: its schedule, and the first step of the backoff."""
JITTER = 90
"""Seconds a scheduled push waits at most, at random, so a team's machines don't push at the same moment."""
MAX_BACKOFF = timedelta(days=1)
SILENT_AFTER = timedelta(days=2)
"""A personal state repository that hasn't taken a push for this long is a doctor finding."""
UNATTENDED = frozenset({"system.daemon"})
"""Modules whose operations run without a person: the OS starts the daemon's ticks."""


def os_family(platform: str = sys.platform) -> Os:
    """The operating system's family, the only thing about the machine an adoption line says."""
    if platform == "darwin":
        return "macos"
    return "linux" if platform.startswith("linux") else "other"


class StateService:
    """Notes presence and versions in the local streams, and pushes them to your personal state repository."""

    def __init__(  # noqa: PLR0913 - the seams tests replace, all but the settings defaulted
        self,
        settings: StateSettings,
        user: str | None,
        state_dir: Path,
        data_dir: Path,
        *,
        run: RunCommand | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = lambda: float(secrets.randbelow(JITTER + 1)),
        version: str = __version__,
    ) -> None:
        """Keep the streams below `state_dir` and the clone below `data_dir`; `for_user` builds it for the kernel."""
        self.settings = settings
        self.user = user
        self.streams = Streams(state_dir)
        self._data_dir = data_dir
        self._run = run
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._sleep = sleep
        self._jitter = jitter
        self._version = version

    @classmethod
    def for_user(cls) -> "StateService":
        """The service with user-scope settings: a project's `hz.toml` can't move the state or name a repository."""
        config = Config.load(Path.home())
        kernel = config.settings(Settings, KERNEL_SECTION)
        return cls(
            config.settings(StateSettings, MODULE_SECTION),
            config.settings(ReportUser, "report").user,
            kernel.state_dir,
            kernel.data_dir,
        )

    # Writing locally -------------------------------------------------------------------------------------------------

    def note_presence(self, module: str) -> bool:
        """Note that hz was used today, unless `module` runs unattended; whether a line was appended.

        Raises:
            HeinzelError: If the stream can't be written.
        """
        if module in UNATTENDED:
            return False
        return self.streams.note_presence(self._clock().date(), self._version)

    def collect(self) -> int:
        """Note this machine's id and its hz version in the adoption stream; how many lines that appended.

        Raises:
            HeinzelError: If a stream can't be written.
        """
        self.streams.machine()
        return int(self.streams.note_version(self._clock().date(), self._version, os_family()) is not None)

    # Pushing ---------------------------------------------------------------------------------------------------------

    @property
    def branch(self) -> str | None:
        """This machine's writer branch, `hz/<handle>/<machine>`; None until `[report] user` is set."""
        return None if self.user is None else f"hz/{self.user}/{self.streams.machine()}"

    def _clone(self, url: str) -> Path:
        # One clone per repository, so naming another repository never pushes the old one's history to it.
        return self._data_dir / "state" / f"personal-{hashlib.sha256(url.encode()).hexdigest()[:12]}"

    def _sink(self) -> Sink:
        url = self.settings.personal
        if url is None:
            msg = (
                "Nothing leaves this machine until you name your own private state repository: "
                "hz system config set system.state.personal https://github.com/<you>/heinzel-state-<you>"
            )
            raise HeinzelError(msg)
        if self.user is None:
            msg = (
                "Your writer branch is named after your handle; set it once: hz system config set report.user <handle>"
            )
            raise HeinzelError(msg)
        machine = self.streams.machine()
        return Sink(
            url, f"hz/{self.user}/{machine}", self._clone(url), self.user, machine, self._run or _runner(self.user)
        )

    def push(self) -> Pushed:
        """Note the version, then push every new line to your personal state repository now.

        Raises:
            HeinzelError: Without a repository or a handle, if a local segment is damaged, or if the push fails or
                was refused; the failure is recorded for `status` and the backoff.
        """
        self.collect()
        sink = self._sink()
        record = self._record(sink.url)
        if record.get("refused"):
            msg = f"Pushing stopped: {record.get('error')}. {self._reset_hint(sink)}"
            raise HeinzelError(msg)
        segments = self.streams.everything()
        if damaged := [problem for segment in segments for problem in problems(segment)]:
            msg = f"Not pushed: {'; '.join(damaged[:3])}; fix or remove those lines in {self.streams.root}"
            raise HeinzelError(msg)
        now = self._clock()
        try:
            outcome = sink.push(segments, self._version)
        except Refused as exc:
            self._save(sink.url, record, now, error=str(exc), refused=True)
            msg = f"Pushing stopped: {exc}. {self._reset_hint(sink)}"
            raise HeinzelError(msg) from exc
        except PushFailed as exc:
            self._save(sink.url, record, now, error=str(exc))
            msg = f"Couldn't push to {sink.url}: {exc}"
            raise HeinzelError(msg) from exc
        self._save(sink.url, record, now, error=None, pushed=True)
        return Pushed(
            repository=sink.url,
            branch=sink.branch,
            lines=outcome.lines,
            segments=outcome.segments,
            commit=outcome.commit,
            pushed=outcome.pushed,
        )

    def scheduled(self) -> JsonValue:
        """The daemon's push: after a random wait of up to 90 s, unless nothing is configured or it backs off.

        Raises:
            HeinzelError: If the push fails, so the daemon's receipt says why.
        """
        self.collect()  # adoption lines are local; they are written whether or not anything leaves
        if self.settings.personal is None or self.user is None:
            return {"skipped": "no personal state repository" if self.user else "no handle in report.user"}
        record = self._record(self.settings.personal)
        if record.get("refused"):
            return {"skipped": "pushing stopped; see hz system state status"}
        if (wait := self._backoff(record)) is not None:
            return {"skipped": f"backing off after {record.get('failures')} failed push(es) until {wait.isoformat()}"}
        self._sleep(self._jitter())
        pushed = self.push()
        return {"pushed": pushed.pushed, "lines": pushed.lines, "segments": pushed.segments}

    def _backoff(self, record: dict[str, Any]) -> datetime | None:
        failures = int(record.get("failures") or 0)
        attempt = _time(record.get("last_attempt"))
        if not failures or attempt is None:
            return None
        until = attempt + min(EVERY * 2 ** (failures - 1), MAX_BACKOFF) - timedelta(minutes=1)
        return until if self._clock() < until else None

    def reset(self) -> StateStatus:
        """Move the clone aside (kept, as evidence) and push again from the local streams, which stay the truth.

        Raises:
            HeinzelError: Without a repository or a handle.
        """
        sink = self._sink()
        if sink.clone.exists():
            sink.clone.rename(sink.clone.with_name(f"{sink.clone.name}.moved-{self._clock():%Y%m%dT%H%M%S}"))
        self._push_file().unlink(missing_ok=True)
        return self.status()

    @staticmethod
    def _reset_hint(sink: Sink) -> str:
        return (
            f"If those commits aren't yours, delete the branch {sink.branch} in {sink.url}, then: hz system state reset"
        )

    # Reading ---------------------------------------------------------------------------------------------------------

    def status(self) -> StateStatus:
        """Where this machine's state goes, what is still to push and how the last push went, without the network.

        Raises:
            HeinzelError: If the local streams can't be read.
        """
        url = self.settings.personal
        record = self._record(url) if url else {}
        clone = self._clone(url) if url else None
        streams: list[StreamStatus] = []
        for stream in STREAMS:
            segments = self.streams.segments(stream)
            lines = sum(s.data.count(b"\n") for s in segments)
            pushed = sum(_pushed_lines(clone, s) for s in segments) if clone else 0
            streams.append(StreamStatus(name=stream, lines=lines, pending=lines - pushed))
        status = StateStatus(
            repository=url,
            branch=self.branch,
            machine=self.streams.machine(),
            streams=streams,
            last_push=_time(record.get("last_push")),
            last_attempt=_time(record.get("last_attempt")),
            failures=int(record.get("failures") or 0),
            error=record.get("error"),
            refused=bool(record.get("refused")),
        )
        if url is None:
            status.next.append(
                "name your own private repository to push to: "
                "hz system config set system.state.personal https://github.com/<you>/heinzel-state-<you>"
            )
        elif self.user is None:
            status.next.append("set your handle, which names your branch: hz system config set report.user <handle>")
        elif status.refused:
            status.next.append("hz system state reset, after deleting the branch if those commits aren't yours")
        elif any(s.pending for s in streams):
            status.next.append("the daemon pushes within 15 minutes; now: hz system state push")
        return status

    def health(self) -> list[Check]:
        """Whether pushes to your personal state repository work; nothing to check when there is none."""
        name = "state repository"
        if self.settings.personal is None:
            return [Check(name=name, status=Status.UP, detail="none named; nothing leaves this machine")]
        try:
            status = self.status()
        except HeinzelError as exc:
            return [Check(name=name, status=Status.DOWN, detail=str(exc), fix=f"check {self.streams.root}")]
        if status.branch is None:
            fix = "hz system config set report.user <handle>"
            return [Check(name=name, status=Status.DEGRADED, detail="no handle names your branch", fix=fix)]
        if status.refused:
            return [Check(name=name, status=Status.DOWN, detail=status.error or "refused", fix="hz system state reset")]
        since = status.last_push or status.last_attempt
        if status.failures and since is not None and self._clock() - since > SILENT_AFTER:
            detail = f"no push since {since:%Y-%m-%d}: {status.error}"
            return [Check(name=name, status=Status.DEGRADED, detail=detail, fix="hz system state push")]
        return [Check(name=name, status=Status.UP, detail=f"{status.branch} in {status.repository}")]

    # The push record -------------------------------------------------------------------------------------------------

    def _push_file(self) -> Path:
        return self.streams.root / PUSH_FILE

    def _record(self, url: str) -> dict[str, Any]:
        """The record of pushes to `url`; empty for another repository, or when it is missing or damaged."""
        try:
            data: object = json.loads(self._push_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        record = cast("dict[str, Any]", data)
        return record if record.get("repository") == url else {}

    def _save(  # noqa: PLR0913 - one record's fields
        self,
        url: str,
        record: dict[str, Any],
        now: datetime,
        *,
        error: str | None,
        refused: bool = False,
        pushed: bool = False,
    ) -> None:
        data = {
            "repository": url,
            "last_attempt": now.isoformat(),
            "last_push": now.isoformat() if pushed else record.get("last_push"),
            "failures": 0 if pushed else int(record.get("failures") or 0) + 1,
            "error": None if error is None else scrub(error),
            "refused": refused,
        }
        path = self._push_file()
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.unlink(missing_ok=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2) + "\n")
        temporary.replace(path)


def _runner(handle: str) -> GitRunner:
    """The hardened runner, with the commit identity pinned so `GIT_AUTHOR_*` and `GIT_COMMITTER_*` can't name you."""
    email = f"{handle}@hz.invalid"
    pinned = tuple(
        (f"GIT_{who}_{what}", value)
        for who in ("AUTHOR", "COMMITTER")
        for what, value in (("NAME", handle), ("EMAIL", email))
    )
    return GitRunner(pinned=pinned)


def _time(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _pushed_lines(clone: Path, segment: Segment) -> int:
    """Lines of `segment` the clone's copy has, that is, the last push carried."""
    copy = clone / segment.stream / segment.name
    if copy.is_symlink() or not copy.is_file():
        return 0
    data = copy.read_bytes()
    return data.count(b"\n") if segment.data.startswith(data) else 0


class LocalPresence(Presence):
    """The `Presence` the kernel injects: a line in the local presence stream the first time hz is used each day."""

    def __init__(self, service: StateService) -> None:
        """Write through `service`."""
        self._service = service

    def mark(self, module: str) -> None:
        """Note today, once; a failure is swallowed here too, since presence never costs an operation."""
        try:
            self._service.note_presence(module)
        except (HeinzelError, OSError):
            return
