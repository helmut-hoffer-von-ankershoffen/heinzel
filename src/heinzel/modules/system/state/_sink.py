"""A sink: a state repository and this machine's writer branch in it, pushed fast-forward only (ADR 0081).

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

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from heinzel.kernel import CommandFailed, RunGitCommand, git_command
from heinzel.modules.system.state._models import HANDLE_PATTERN, MACHINE, STREAMS, VERSION_PATTERN
from heinzel.modules.system.state._streams import MAX_SEGMENT, SEGMENT, Segment, problems

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


class StateManifest(BaseModel):
    """The exact metadata envelope permitted in a writer tree."""

    model_config = ConfigDict(extra="forbid", strict=True)
    v: int = Field(ge=1, le=1)
    user: str = Field(pattern=HANDLE_PATTERN, max_length=39)
    machine: str = Field(pattern=MACHINE.pattern)
    hz: str = Field(pattern=VERSION_PATTERN)
    streams: list[str]


class Ownership(BaseModel):
    """Private receipts of commits created here, persisted before any network push."""

    model_config = ConfigDict(extra="forbid", strict=True)
    repository: str
    branch: str
    commits: list[str] = Field(default_factory=list)
    confirmed: str | None = None


@dataclass(frozen=True, slots=True)
class Sink:
    """One state repository, this machine's branch in it, and the local clone that holds only that branch."""

    url: str
    branch: str
    clone: Path
    handle: str
    machine: str
    run: RunGitCommand

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
                owned = self._ownership()
                owned.commits.append(self._git("rev-parse", "HEAD").strip())
                self._save_ownership(owned)
            head = self._quietly("rev-parse", "-q", "--verify", "HEAD")
            if head is None or head == remote:
                return Outcome(lines=lines, segments=changed, commit=head, pushed=False)
            ref = f"refs/heads/{self.branch}"
            self._git("push", "-q", REMOTE, f"{ref}:{ref}")
            owned = self._ownership()
            owned.confirmed = head
            self._save_ownership(owned)
        except UnicodeError as exc:
            msg = "branch tree contains invalid text (HZS001)"
            raise Refused(msg) from exc
        except CommandFailed as exc:
            error = scrub(exc.stderr)
            if any(marker in error for marker in _REJECTED):
                msg = f"the branch {self.branch} moved to commits this machine didn't make (HZS001): {error}"
                raise Refused(msg) from exc
            raise PushFailed(error or "git failed without saying why") from exc
        return Outcome(lines=lines, segments=changed, commit=head, pushed=True)

    def _ownership(self) -> Ownership:
        path = self.clone / ".git" / "hz-owned.json"
        try:
            record = Ownership.model_validate_json(path.read_bytes())
        except FileNotFoundError:
            return Ownership(repository=self.url, branch=self.branch)
        except (OSError, ValidationError) as exc:
            msg = "private commit ownership record is damaged (HZS001)"
            raise Refused(msg) from exc
        if (record.repository, record.branch) != (self.url, self.branch):
            msg = "private commit ownership belongs to another sink (HZS001)"
            raise Refused(msg)
        return record

    def _save_ownership(self, record: Ownership) -> None:
        write_private(self.clone / ".git" / "hz-owned.json", record.model_dump_json().encode())

    @property
    def confirmed(self) -> str | None:
        """The last commit whose push this machine confirmed; never a failed push's worktree."""
        return self._ownership().confirmed if (self.clone / ".git").is_dir() else None

    def read_segment(self, commit: str, path: str) -> bytes:
        """Read a segment from an immutable commit, without consulting the working tree.

        Raises:
            CommandFailed: If the commit has no such segment.
        """
        return self._git("show", f"{commit}:{path}").encode()

    def _prepare(self) -> str | None:
        """Fetch without checkout and refuse every remote commit absent from private local ownership receipts.

        Raises:
            Refused: If local ownership or the remote tree or ancestry is unsafe.
        """
        if self.clone.is_symlink() or (self.clone / ".git").is_symlink():
            msg = "the state clone is a symbolic link (HZS001)"
            raise Refused(msg)
        if (self.clone / ".git").exists() and not (self.clone / ".git").is_dir():
            msg = "the state clone has an indirect Git directory (HZS001)"
            raise Refused(msg)
        if not (self.clone / ".git").is_dir():
            self.clone.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._git("init", "-q")
            self._git("remote", "add", REMOTE, self.url)
        self._git("symbolic-ref", "HEAD", f"refs/heads/{self.branch}")
        self._git("remote", "set-url", REMOTE, self.url)
        owned = self._ownership()
        head = self._quietly("rev-parse", "-q", "--verify", "HEAD")
        if head is not None:
            if head not in owned.commits:
                msg = "local HEAD has no private ownership receipt (HZS001)"
                raise Refused(msg)
            self._validate_tree(head)
        ref = f"refs/heads/{self.branch}"
        listed = self._git("ls-remote", "--heads", REMOTE, ref).split()
        if not listed:
            if owned.confirmed:
                msg = "the previously pushed remote branch was deleted (HZS001)"
                raise Refused(msg)
            return None
        tracking = f"refs/remotes/{REMOTE}/{self.branch}"
        self._git("fetch", "-q", "--no-tags", REMOTE, f"+{ref}:{tracking}")
        remote = self._git("rev-parse", tracking).strip()
        self._validate_tree(remote)
        if remote not in owned.commits or head is None or not self._ancestor(remote, head):
            msg = "remote branch has commits this machine did not create (HZS001)"
            raise Refused(msg)
        if owned.confirmed and not self._ancestor(owned.confirmed, remote):
            msg = "remote branch was rewound (HZS001)"
            raise Refused(msg)
        # A prior push may have reached the server before the process could save its acknowledgement.
        owned.confirmed = remote
        self._save_ownership(owned)
        return remote

    def _validate_tree(self, commit: str) -> None:
        manifest_found = False
        for entry in self._git("ls-tree", "-r", "-z", commit).split("\0"):
            if not entry:
                continue
            metadata, path = entry.split("\t", 1)
            mode, kind, oid = metadata.split()
            if mode != "100644" or kind != "blob":
                msg = "branch tree has a link or non-file entry (HZS001)"
                raise Refused(msg)
            if int(self._git("cat-file", "-s", oid)) > MAX_SEGMENT:
                msg = "branch tree has an oversized file (HZS001)"
                raise Refused(msg)
            data = self._git("cat-file", "blob", oid).encode()
            if path == MANIFEST:
                self._validate_manifest(data)
                manifest_found = True
                continue
            parts = path.split("/")
            if len(parts) != len(("stream", "month")) or parts[0] not in STREAMS or not SEGMENT.fullmatch(parts[1]):
                msg = "branch tree contains an unexpected path (HZS001)"
                raise Refused(msg)
            if problems(Segment(parts[0], parts[1], data)):
                msg = "branch tree contains malformed stream data (HZS001)"
                raise Refused(msg)

        if not manifest_found:
            msg = "branch tree has no manifest (HZS001)"
            raise Refused(msg)

    def _validate_manifest(self, data: bytes) -> None:
        try:
            manifest = StateManifest.model_validate_json(data).model_dump()
        except ValidationError as exc:
            msg = "branch manifest is malformed (HZS001)"
            raise Refused(msg) from exc
        if manifest != self._manifest(str(manifest["hz"])):
            msg = "branch manifest differs from this sink (HZS001)"
            raise Refused(msg)

    def _manifest(self, hz: str) -> dict[str, object]:
        return {"v": MANIFEST_VERSION, "user": self.handle, "machine": self.machine, "hz": hz, "streams": [*STREAMS]}

    def _ancestor(self, older: str, newer: str) -> bool:
        return self._quietly("merge-base", "--is-ancestor", older, newer) is not None

    def _copy(self, segments: Sequence[Segment], hz: str) -> tuple[int, int]:
        """Write the local segments into the clone; the new lines and the segments that changed.

        Raises:
            Refused: If a segment on the branch isn't a prefix of the local one.
        """
        local = {segment.path: segment.data for segment in segments}
        existing = self._branch_segments()
        head = self._quietly("rev-parse", "-q", "--verify", "HEAD")
        committed: dict[str, bytes] = {}
        if head is not None:
            # The worktree is a cache: deleting or shortening it must never erase committed stream history.
            committed.update(
                (path, self.read_segment(head, path))
                for path in self._git("ls-tree", "-r", "--name-only", head).splitlines()
                if path != MANIFEST
            )
        for path, data in (*existing.items(), *committed.items()):
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
            write_private(target, segment.data)
            lines += segment.data.count(b"\n") - before.count(b"\n")
            changed += 1
        manifest = self._manifest(hz)
        content = (json.dumps(manifest, indent=2) + "\n").encode()
        target = self.clone / MANIFEST
        if not target.is_file() or target.read_bytes() != content:
            write_private(target, content)
        return lines, changed

    def _branch_segments(self) -> dict[str, bytes]:
        """The stream segments in the clone's working tree, by path; links and other files there are refused.

        Raises:
            Refused: If a stream directory or segment is a link, or a segment is too large.
        """
        found: dict[str, bytes] = {}
        if (self.clone / MANIFEST).is_symlink():
            msg = "the clone manifest is a symbolic link (HZS001)"
            raise Refused(msg)
        if any(path.name not in {*STREAMS, MANIFEST, ".git"} for path in self.clone.iterdir()):
            msg = "the clone contains an unexpected path (HZS001)"
            raise Refused(msg)
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
                if not path.is_file() or not SEGMENT.fullmatch(path.name):
                    msg_0 = "the clone contains an unexpected segment path (HZS001)"
                    raise Refused(msg_0)
                if path.is_file() and SEGMENT.fullmatch(path.name):
                    found[f"{stream}/{path.name}"] = path.read_bytes()
        return found


def write_private(target: Path, data: bytes) -> None:
    """Write `data` to a fresh file beside `target`, then move it into place."""
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        Path(name).replace(target)
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
