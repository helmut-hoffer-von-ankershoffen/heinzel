"""The state module's service: note presence and adoption locally, push them to your personal state repository.

Writing is local and always on: a line a day, and a line per version, in your state directory. Nothing leaves the
machine until you name your own private repository in `[system.state] personal`; then the daemon's `push` job, every
15 minutes with up to 90 seconds of jitter, copies what is new to this machine's writer branch (ADR 0081). A failed
push backs off, doubling up to a day; a branch that holds what this machine didn't write stops pushing until
`hz system state reset`. The service reads user-scope configuration only, so a project's `hz.toml` can't redirect it.
"""

import fcntl
import hashlib
import os
import secrets
import sys
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import JsonValue, ValidationError

from heinzel import Check, Config, HeinzelError, JobInactive, Settings, Status, __version__
from heinzel.kernel import KERNEL_SECTION, CommandFailed, GitRunner, Presence, RunGitCommand
from heinzel.modules.system.state._models import (
    STREAMS,
    Os,
    Pushed,
    PushRecord,
    ReportUser,
    StateSettings,
    StateStatus,
    StreamStatus,
)
from heinzel.modules.system.state._sink import PushFailed, Refused, Sink, scrub, write_private
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
        run: RunGitCommand | None = None,
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
        return (
            self._data_dir / "state" / f"personal-{hashlib.sha256(f'{url}\0{self.branch}'.encode()).hexdigest()[:12]}"
        )

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
            url,
            f"hz/{self.user}/{machine}",
            self._clone(url),
            self.user,
            machine,
            self._run or _runner(self.user, self._clock()),
        )

    def push(self) -> Pushed:
        """Note the version, then push every new line to your personal state repository now.

        Raises:
            HeinzelError: Without a repository or a handle, if a local segment is damaged, or if the push fails or
                was refused; the failure is recorded for `status` and the backoff.
        """
        with self._locked():
            return self._push()

    def _push(self) -> Pushed:
        self.collect()
        sink = self._sink()
        record = self._record(sink.url)
        if record.refused:
            msg = f"Pushing stopped: {record.error}. {self._reset_hint(sink)}"
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

    def scheduled(self) -> JsonValue | JobInactive:
        """The daemon's push: after a random wait of up to 90 s, unless nothing is configured or it backs off.

        Raises:
            HeinzelError: If the push fails, so the daemon's receipt says why.
        """
        self.collect()  # adoption lines are local; they are written whether or not anything leaves
        if self.settings.personal is None or self.user is None:
            return JobInactive(reason="no personal state repository" if self.user else "no handle in report.user")
        record = self._record(self.settings.personal)
        if record.refused:
            msg = f"Pushing stopped: {record.error}; see hz system state status (HZS001)"
            raise HeinzelError(msg)
        if (wait := self._backoff(record)) is not None:
            return JobInactive(reason=f"backing off after {record.failures} failed push(es) until {wait.isoformat()}")
        self._sleep(self._jitter())
        pushed = self.push()
        return {"pushed": pushed.pushed, "lines": pushed.lines, "segments": pushed.segments}

    def _backoff(self, record: PushRecord) -> datetime | None:
        failures = record.failures
        attempt = record.last_attempt
        if not failures or attempt is None:
            return None
        until = attempt + min(EVERY * 2 ** min(failures - 1, 7), MAX_BACKOFF) - timedelta(minutes=1)
        return until if self._clock() < until else None

    def reset(self) -> StateStatus:
        """Move the clone aside (kept, as evidence) and push again from the local streams, which stay the truth.

        Raises:
            HeinzelError: Without a repository or a handle.
        """
        with self._locked():
            self._reset()
            return self.status()

    def _reset(self) -> None:
        sink = self._sink()
        if sink.clone.exists():
            sink.clone.rename(sink.clone.with_name(f"{sink.clone.name}.moved-{secrets.token_hex(8)}"))
        self._push_file().unlink(missing_ok=True)

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
        record = self._record(url or "")
        sink = self._sink() if url and self.user else None
        try:
            confirmed = sink.confirmed if sink else None
        except Refused as exc:
            confirmed = None
            record.refused = True
            record.error = str(exc)
        streams: list[StreamStatus] = []
        for stream in STREAMS:
            segments = self.streams.segments(stream)
            lines = sum(s.data.count(b"\n") for s in segments)
            pushed = sum(_pushed_lines(sink, confirmed, s) for s in segments) if sink and confirmed else 0
            streams.append(StreamStatus(name=stream, lines=lines, pending=lines - pushed))
        status = StateStatus(
            repository=url,
            branch=self.branch,
            machine=self.streams.machine(),
            streams=streams,
            last_push=record.last_push,
            last_attempt=record.last_attempt,
            failures=record.failures,
            error=record.error,
            refused=record.refused,
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
        except (HeinzelError, OSError, Refused) as exc:
            return [Check(name=name, status=Status.DOWN, detail=str(exc), fix=f"check {self.streams.root}")]
        if status.branch is None:
            fix = "hz system config set report.user <handle>"
            return [Check(name=name, status=Status.DEGRADED, detail="no handle names your branch", fix=fix)]
        if status.refused:
            return [Check(name=name, status=Status.DOWN, detail=status.error or "refused", fix="hz system state reset")]
        since = status.last_push
        if since is None or (status.failures and self._clock() - since > SILENT_AFTER):
            detail = (
                "this repository has never taken a push"
                if since is None
                else f"no push since {since:%Y-%m-%d}: {status.error}"
            )
            return [Check(name=name, status=Status.DEGRADED, detail=detail, fix="hz system state push")]
        return [Check(name=name, status=Status.UP, detail=f"{status.branch} in {status.repository}")]

    # The push record -------------------------------------------------------------------------------------------------

    def _push_file(self) -> Path:
        return self.streams.root / PUSH_FILE

    @contextmanager
    def _locked(self) -> Generator[None]:
        # One outer lock, then machine/stream locks; reset never removes this stable lock inode.
        self.streams.machine()
        path = self.streams.root / "push.lock"
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "r+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def _record(self, url: str) -> PushRecord:
        empty = PushRecord(repository=url, branch=self.branch or "")
        try:
            record = PushRecord.model_validate_json(self._push_file().read_bytes())
        except FileNotFoundError:
            return empty
        except (OSError, ValidationError):
            empty.refused = True
            empty.error = "private push record is damaged (HZS001)"
            return empty
        return record if (record.repository, record.branch) == (url, self.branch) else empty

    def _save(  # noqa: PLR0913 - one record's fields
        self,
        url: str,
        record: PushRecord,
        now: datetime,
        *,
        error: str | None,
        refused: bool = False,
        pushed: bool = False,
    ) -> None:
        data = PushRecord(
            repository=url,
            branch=self.branch or "",
            last_attempt=now,
            last_push=now if pushed else record.last_push,
            failures=0 if pushed else record.failures + 1,
            error=None if error is None else scrub(error),
            refused=refused,
        )
        write_private(self._push_file(), data.model_dump_json().encode())


def _runner(handle: str, now: datetime | None = None) -> GitRunner:
    """The hardened runner, with the commit identity pinned so `GIT_AUTHOR_*` and `GIT_COMMITTER_*` can't name you."""
    email = f"{handle}@hz.invalid"
    day = f"{now or datetime.now().astimezone():%Y-%m-%d}T00:00:00+00:00"
    pinned = tuple(
        (f"GIT_{who}_{what}", value)
        for who in ("AUTHOR", "COMMITTER")
        for what, value in (("NAME", handle), ("EMAIL", email), ("DATE", day))
    )
    return GitRunner(pinned=pinned)


def _pushed_lines(sink: Sink, commit: str, segment: Segment) -> int:
    """Lines contained in a confirmed remote commit, regardless of unpushed worktree changes."""
    try:
        data = sink.read_segment(commit, segment.path)
    except CommandFailed:
        return 0
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
