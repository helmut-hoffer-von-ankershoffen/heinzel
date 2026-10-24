"""A sink: a state repository and this machine's writer branch in it, pushed fast-forward only (ADR 0062).

The sink keeps a clone below the data directory that holds only its own branch, `hz/<handle>/<machine>`, an orphan
with exactly one writer: this machine. A push fetches that branch, checks that every segment on it is a prefix of
the local segment of the same name, copies the local bytes over, commits once and pushes without force. Whatever
else is found (a branch that moved to commits this machine didn't make, a segment with lines it never wrote) stops
the sink and is reported; it is never merged, rebased or overwritten.

Every git command goes through the kernel's hardened runner (no prompts, no submodules, no `ext::`, a scrubbed
environment, a timeout), with hooks off and a fixed identity, so a commit names the handle and nothing of the person.
Credentials are git's own (a credential helper such as `gh auth setup-git`, or ssh); hz never reads or keeps one.
"""

import json
import os
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from heinzel.kernel import CommandFailed, RunCommand, git_command
from heinzel.modules.system.state._models import STREAMS
from heinzel.modules.system.state._streams import MAX_SEGMENT, SEGMENT, Segment

MANIFEST = "hz-state.json"
MANIFEST_VERSION = 1
REMOTE = "origin"
MAX_ERROR = 2000
_USERINFO = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/@\s]+@")
_REJECTED = ("[rejected]", "non-fast-forward", "fetch first", "stale info")


def scrub(text: str) -> str:
    """Git's error output with any `user:password@` taken out of URLs, cut to a readable length."""
    return _USERINFO.sub(r"\1", text).strip()[-MAX_ERROR:]


class PushFailed(Exception):  # noqa: N818 - says what happened
    """A push that didn't reach the repository: the network, the credentials, a missing repository. Retried later."""


class Refused(Exception):  # noqa: N818 - says what happened
    """The branch holds what this machine didn't write; pushing stops until `hz system state reset`."""


@dataclass(frozen=True, slots=True)
class Outcome:
    """What a push did."""

    lines: int
    segments: int
    commit: str | None
    pushed: bool


@dataclass(frozen=True, slots=True)
class Sink:
    """One state repository, this machine's branch in it, and the local clone that holds only that branch."""

    url: str
    branch: str
    clone: Path
    handle: str
    machine: str
    run: RunCommand

    def _git(self, *parts: str) -> str:
        options = (
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "-c",
            f"user.name={self.handle}",
            "-c",
            f"user.email={self.handle}@hz.invalid",
            "-c",
            "core.autocrlf=false",
        )
        return self.run(git_command("-C", self.clone, *options, *parts), None)

    def _quietly(self, *parts: str) -> str | None:
        try:
            return self._git(*parts).strip()
        except CommandFailed:
            return None

    def push(self, segments: Sequence[Segment], hz: str) -> Outcome:
        """Make the branch hold exactly the local segments' bytes and push it, fast-forward only.

        Raises:
            PushFailed: If git can't reach or write the repository.
            Refused: If the branch holds commits or lines this machine didn't write.
        """
        try:
            remote = self._prepare()
            lines, changed = self._copy(segments, hz)
            paths = [name for name in (*STREAMS, MANIFEST) if (self.clone / name).exists()]
            self._git("add", "-A", "--", *paths)
            if self._git("status", "--porcelain", "--", *paths).strip():
                self._git("commit", "-q", "-m", f"state: {lines} new line(s) in {changed} segment(s)")
            head = self._quietly("rev-parse", "-q", "--verify", "HEAD")
            if head is None or head == remote:
                return Outcome(lines=lines, segments=changed, commit=head, pushed=False)
            ref = f"refs/heads/{self.branch}"
            self._git("push", "-q", REMOTE, f"{ref}:{ref}")
        except CommandFailed as exc:
            error = scrub(exc.stderr)
            if any(marker in error for marker in _REJECTED):
                msg = f"the branch {self.branch} moved to commits this machine didn't make (HZS001): {error}"
                raise Refused(msg) from exc
            raise PushFailed(error or "git failed without saying why") from exc
        return Outcome(lines=lines, segments=changed, commit=head, pushed=True)

    def _prepare(self) -> str | None:
        """Make the clone's branch the remote's tip, or keep it when it is ahead; the remote's commit, if any.

        Raises:
            Refused: If the two diverged.
        """
        if not (self.clone / ".git").is_dir():
            self.clone.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._git("init", "-q")
            self._git("remote", "add", REMOTE, self.url)
            self._git("symbolic-ref", "HEAD", f"refs/heads/{self.branch}")
        ref = f"refs/heads/{self.branch}"
        listed = self._git("ls-remote", "--heads", REMOTE, ref).split()
        if not listed:
            return None  # a new machine: its first push creates the orphan branch
        remote = listed[0]
        tracking = f"refs/remotes/{REMOTE}/{self.branch}"
        self._git("fetch", "-q", "--no-tags", REMOTE, f"+{ref}:{tracking}")
        head = self._quietly("rev-parse", "-q", "--verify", "HEAD")
        if head == remote or (head is not None and self._ancestor(remote, head)):
            return remote  # current, or ahead with a commit an earlier push didn't get out
        if head is None or self._ancestor(head, remote):
            self._git("reset", "-q", "--hard", tracking)  # catch up; `_copy` checks every segment it brought
            return remote
        msg = f"the branch {self.branch} and this machine's clone of it diverged (HZS001)"
        raise Refused(msg)

    def _ancestor(self, older: str, newer: str) -> bool:
        return self._quietly("merge-base", "--is-ancestor", older, newer) is not None

    def _copy(self, segments: Sequence[Segment], hz: str) -> tuple[int, int]:
        """Write the local segments into the clone; the new lines and the segments that changed.

        Raises:
            Refused: If a segment on the branch isn't a prefix of the local one.
        """
        local = {segment.path: segment.data for segment in segments}
        for path, data in self._branch_segments().items():
            if not local.get(path, b"").startswith(data):
                msg = (
                    f"{path} on the branch {self.branch} has lines this machine's {path} doesn't (HZS001): a writer "
                    "branch has one writer, and a local stream only grows"
                )
                raise Refused(msg)
        lines = changed = 0
        for segment in segments:
            target = self.clone / segment.stream / segment.name
            before = target.read_bytes() if target.is_file() else b""
            if before == segment.data:
                continue
            _write(target, segment.data)
            lines += segment.data.count(b"\n") - before.count(b"\n")
            changed += 1
        manifest = {
            "v": MANIFEST_VERSION,
            "user": self.handle,
            "machine": self.machine,
            "hz": hz,
            "streams": [*STREAMS],
        }
        content = (json.dumps(manifest, indent=2) + "\n").encode()
        target = self.clone / MANIFEST
        if not target.is_file() or target.read_bytes() != content:
            _write(target, content)
        return lines, changed

    def _branch_segments(self) -> dict[str, bytes]:
        """The stream segments in the clone's working tree, by path; links and other files there are refused.

        Raises:
            Refused: If a stream directory or segment is a link, or a segment is too large.
        """
        found: dict[str, bytes] = {}
        for stream in STREAMS:
            directory = self.clone / stream
            if directory.is_symlink():
                msg = f"{stream} on the branch {self.branch} is a symbolic link (HZS001)"
                raise Refused(msg)
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                if path.is_symlink() or (path.is_file() and path.stat().st_size > MAX_SEGMENT):
                    msg = f"{stream}/{path.name} on the branch {self.branch} isn't a segment hz wrote (HZS001)"
                    raise Refused(msg)
                if path.is_file() and SEGMENT.fullmatch(path.name):
                    found[f"{stream}/{path.name}"] = path.read_bytes()
        return found


def _write(target: Path, data: bytes) -> None:
    """Write `data` to a fresh file beside `target`, then move it into place."""
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        Path(name).replace(target)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
