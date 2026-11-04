"""Durable generic sessions, with a rebuildable SQLite metadata index (ADR 0049).

Spines are authoritative. The index is committed only after a spine append is durable; stale watermarks are repaired
on open. The private root is controlled by its owner: defending against that same user replacing directories while
SQLite opens them is outside the boundary, just as an installed plugin already executes with that user's rights.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
from collections.abc import Callable, Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import JsonValue, TypeAdapter, ValidationError

from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._session import SessionEvent, SessionHead, SessionProjector, SessionRead
from heinzel.kernel._session_io import (
    ID,
    JSON_VALUE,
    MAX_LINE,
    PRIVATE_DIR,
    PRIVATE_FILE,
    append_line,
    child,
    directory,
    encoded,
    identifier,
    open_file,
    read_checkpoint,
    replace_checkpoint,
    scan,
    temporary_name,
)

_MAX_INDEXED_PAGE = 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    session TEXT PRIMARY KEY, seq INTEGER NOT NULL, hash TEXT, size INTEGER NOT NULL,
    mtime INTEGER NOT NULL, inode INTEGER NOT NULL, updated TEXT, projection TEXT NOT NULL, problem TEXT
);
CREATE TABLE IF NOT EXISTS events (
    session TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL, turn TEXT, key TEXT, event TEXT NOT NULL,
    PRIMARY KEY (session, seq)
);
CREATE TABLE IF NOT EXISTS digest_meta (version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS event_lines (
    session TEXT NOT NULL, seq INTEGER NOT NULL, digest TEXT NOT NULL, raw BLOB NOT NULL, PRIMARY KEY (session, seq)
);
CREATE INDEX IF NOT EXISTS event_keys ON events(session, kind, key);
CREATE INDEX IF NOT EXISTS event_turns ON events(session, turn, seq);
"""
PROJECTION = TypeAdapter(dict[str, JsonValue])


def _clock() -> datetime:
    return datetime.now(UTC)


def _last_data(_previous: dict[str, JsonValue], event: SessionEvent) -> dict[str, JsonValue]:
    return event.data


def _read_spine(folder: int) -> SessionRead:
    try:
        return scan(folder)
    except (OSError, HeinzelError):
        return SessionRead(problem="Session spine is not a readable private regular file")


def _line_digest(raw: object, digest: object) -> str:
    if not isinstance(raw, bytes) or not isinstance(digest, str) or hashlib.sha256(raw).hexdigest() != digest:
        msg = "The indexed session event differs from its checkpoint; rebuild or repair it"
        raise HeinzelError(msg)
    return digest


def _raw_lines(folder: int, result: SessionRead) -> list[bytes]:
    if not result.events:
        return []
    descriptor = open_file(folder, "session.jsonl", os.O_RDONLY)
    with os.fdopen(descriptor, "rb") as handle:
        lines: list[bytes] = []
        for digest in result.hashes:
            line = handle.readline(MAX_LINE + 1)
            if hashlib.sha256(line).hexdigest() != digest:
                msg = "The session changed during index rebuild; retry after it settles"
                raise HeinzelError(msg)
            lines.append(line)
        return lines


def _indexed_event(raw: str | bytes) -> SessionEvent:
    try:
        return SessionEvent.model_validate_json(raw)
    except (ValueError, ValidationError):
        msg = "The indexed session contains an invalid event; rebuild or repair it"
        raise HeinzelError(msg) from None


def _page_anchor(
    index: sqlite3.Connection, session: str, position: tuple[int, str | None], anchor: tuple[int, str | None] | None
) -> tuple[int, str | None]:
    row = index.execute("SELECT seq, hash, problem FROM sessions WHERE session = ?", (session,)).fetchone()
    if row is None or row["problem"]:
        msg = "The session index is unavailable or needs repair"
        raise HeinzelError(msg)
    after, previous = position
    high, high_hash = anchor if anchor is not None else (int(row["seq"]), row["hash"])
    if type(high) is not int or not 0 <= after <= high <= int(row["seq"]):
        msg = "Invalid indexed session page"
        raise HeinzelError(msg)
    if (after == 0) != (previous is None) or (high == 0) != (high_hash is None):
        msg = "Invalid indexed session page"
        raise HeinzelError(msg)
    if high:
        found = index.execute(
            "SELECT digest, raw FROM event_lines WHERE session = ? AND seq = ?", (session, high)
        ).fetchone()
        if found is None or _line_digest(found["raw"], found["digest"]) != high_hash:
            msg = "The indexed session checkpoint differs; rebuild or repair the session"
            raise HeinzelError(msg)
    return high, high_hash


def _page_events(
    index: sqlite3.Connection,
    session: str,
    position: tuple[int, str | None],
    anchor: tuple[int, str | None],
    limit: int,
) -> SessionRead:
    after, previous = position
    high, high_hash = anchor
    rows = index.execute(
        "SELECT event_lines.raw, event_lines.digest FROM events LEFT JOIN event_lines "
        "ON events.session = event_lines.session AND events.seq = event_lines.seq "
        "WHERE events.session = ? AND events.seq > ? AND events.seq <= ? ORDER BY events.seq LIMIT ?",
        (session, after, high, limit),
    ).fetchall()
    result = SessionRead()
    for item in rows:
        digest = _line_digest(item["raw"], item["digest"])
        event = _indexed_event(item["raw"])
        if event.seq != after + 1 or event.prev != previous:
            msg = "The indexed session chain differs; rebuild or repair the session"
            raise HeinzelError(msg)
        previous = digest
        after = event.seq
        result.events.append(event)
        result.hashes.append(previous)
    if after < high and len(rows) < limit:
        msg = "The indexed session is incomplete; rebuild or repair the session"
        raise HeinzelError(msg)
    if after == high and previous != high_hash:
        msg = "The indexed session checkpoint differs; rebuild or repair the session"
        raise HeinzelError(msg)
    if after < high:
        following = index.execute(
            "SELECT raw, digest FROM event_lines WHERE session = ? AND seq = ?", (session, after + 1)
        ).fetchone()
        if following is None:
            msg = "The indexed session is incomplete; rebuild or repair the session"
            raise HeinzelError(msg)
        _line_digest(following["raw"], following["digest"])
        next_event = _indexed_event(following["raw"])
        if next_event.seq != after + 1 or next_event.prev != previous:
            msg = "The indexed session chain differs; rebuild or repair the session"
            raise HeinzelError(msg)
    return result


def _read_body(parent: int, turn: str, expected_hash: str | None) -> list[JsonValue]:
    identifier(turn)
    if expected_hash is not None and re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None:
        msg = "A body checkpoint hash must be a SHA-256 hex digest"
        raise HeinzelError(msg)
    with child(parent, "turns") as folder:
        descriptor = open_file(folder, f"{turn}.jsonl", os.O_RDONLY)
        with os.fdopen(descriptor, "rb") as handle:
            values: list[JsonValue] = []
            digest = hashlib.sha256()
            for line in handle:
                digest.update(line)
                if not line.endswith(b"\n"):
                    msg = "The turn body ends with an incomplete record"
                    raise HeinzelError(msg)
                try:
                    values.append(JSON_VALUE.validate_json(line))
                except ValidationError:
                    msg = "The turn body contains an invalid JSON record"
                    raise HeinzelError(msg) from None
            if expected_hash is not None and digest.hexdigest() != expected_hash:
                msg = "The turn body differs from its saved checkpoint; restore the body before resuming"
                raise HeinzelError(msg)
            return values


@contextlib.contextmanager
def _lock(parent: int, name: str, *, wait: bool) -> Generator[None]:
    descriptor = open_file(parent, name, os.O_RDWR | os.O_CREAT)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            msg = "This session has another writer; retry after its current turn finishes"
            raise HeinzelError(msg) from None
        yield
    finally:
        os.close(descriptor)


class SessionStore:
    """Private append-only sessions, independent of a model provider and of module vocabularies."""

    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], datetime] = _clock,
        project: SessionProjector = _last_data,
        projection_version: str = "last-data-v1",
    ) -> None:
        """Use `root`, with an injected clock and a pure metadata projection; create nothing until first use."""
        self.root = root.absolute()
        self.clock = clock
        self.project = project
        self.projection_version = projection_version

    @contextlib.contextmanager
    def writer(self, session: str, *, wait: bool = False) -> Generator["SessionWriter"]:
        """Hold one session's writer lock across a turn; index locks are held only during checkpoints.

        `wait=True` is for short control/catalog journals whose callers never acquire a long turn writer while
        holding them. The default refuses a busy writer immediately, preserving one active turn per session.

        Yields:
            The writer, valid only until the context exits.

        Raises:
            HeinzelError: If the id, directory or lock is unsafe, or another writer holds this session.
            OSError: If the private storage cannot be opened.
        """
        identifier(session)
        with (
            directory(self.root) as root,
            child(root, session, create=True) as folder,
            _lock(folder, "lock", wait=wait),
        ):
            writer = SessionWriter(self, root, folder, session)
            try:
                yield writer
            finally:
                writer.active = False

    def heads(self) -> list[SessionHead]:
        """List indexed metadata; unchanged spines are checked by stat, never opened.

        Raises:
            HeinzelError: If storage is unsafe or the projection fails.
            OSError: If storage cannot be read.
        """
        with directory(self.root) as root, self._index(root) as index:
            return [self._head(row) for row in index.execute("SELECT * FROM sessions ORDER BY session")]

    def read(self, session: str, *, kinds: set[str] | None = None) -> SessionRead:
        """Read a verified prefix and damage report, filtering unknown kinds only after chain verification.

        Raises:
            HeinzelError: If the id or file is unsafe.
            OSError: If the private storage cannot be read.
        """
        identifier(session)
        with directory(self.root) as root, self._index(root) as index:
            row = index.execute("SELECT problem FROM sessions WHERE session = ?", (session,)).fetchone()
            with child(root, session) as folder:
                result = _read_spine(folder)
            if row and row["problem"]:
                result.problem = str(row["problem"])
            if kinds is not None:
                pairs = [
                    (event, digest)
                    for event, digest in zip(result.events, result.hashes, strict=True)
                    if event.kind in kinds
                ]
                result.events = [event for event, _ in pairs]
                result.hashes = [digest for _, digest in pairs]
            return result

    def indexed_page(
        self, session: str, *, position: tuple[int, str | None], anchor: tuple[int, str | None] | None, limit: int
    ) -> tuple[SessionRead, tuple[int, str | None]]:
        """Read a bounded contiguous indexed page; damaged or mismatched indexes fail closed.

        Raises:
            HeinzelError: If the index, anchor, page, or session is invalid.
            OSError: If private storage cannot be read.
        """
        identifier(session)
        if (
            type(position[0]) is not int
            or position[0] < 0
            or type(limit) is not int
            or not 1 <= limit <= _MAX_INDEXED_PAGE
        ):
            msg = "Invalid indexed session page"
            raise HeinzelError(msg)
        with directory(self.root) as root, self._index(root) as index:
            chosen = _page_anchor(index, session, position, anchor)
            return _page_events(index, session, position, chosen, limit), chosen

    def reindex(self) -> list[SessionHead]:
        """Rebuild metadata from all spines, preserving damage reports and the original files.

        Raises:
            HeinzelError: If storage is unsafe or the projection fails.
            OSError: If storage cannot be read.
        """
        with directory(self.root) as root, self._index(root) as index:
            self._sync(root, index, force=True)
            index.commit()
            return [self._head(row) for row in index.execute("SELECT * FROM sessions ORDER BY session")]

    def body_inventory(
        self, session: str, *, known: tuple[int, int, int, int] | None
    ) -> tuple[tuple[int, int, int, int] | None, frozenset[str] | None]:
        """List retained private body IDs only when their directory changes.

        Raises:
            HeinzelError: If storage is unsafe or changes while it is being inspected.
            OSError: If private storage cannot be read.
        """
        identifier(session)
        try:
            with directory(self.root) as root, child(root, session) as folder, child(folder, "turns") as turns:
                before = os.fstat(turns)
                signature = (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns)
                if signature == known:
                    return signature, None
                ids: set[str] = set()
                with os.scandir(turns) as entries:
                    for entry in entries:
                        if not entry.name.endswith(".jsonl"):
                            continue
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except FileNotFoundError:
                            msg = "Retained message bodies changed during inspection; retry the search"
                            raise HeinzelError(msg) from None
                        if (
                            stat.S_ISREG(info.st_mode)
                            and info.st_uid == os.getuid()
                            and stat.S_IMODE(info.st_mode) == PRIVATE_FILE
                            and info.st_nlink == 1
                        ):
                            ids.add(entry.name.removesuffix(".jsonl"))
                after = os.fstat(turns)
                if signature != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns):
                    msg = "Retained message bodies changed during inspection; retry the search"
                    raise HeinzelError(msg)
                return signature, frozenset(ids)
        except FileNotFoundError:
            return None, frozenset()

    def body(self, session: str, turn: str, *, expected_hash: str) -> list[JsonValue]:
        """Read a committed body without taking a writer lock, verifying its exact saved bytes.

        Use a body id and hash from a verified checkpoint. A caller using immutable body ids can read a committed
        snapshot while another turn writes the session. A changed or incomplete body is refused, never returned.

        Raises:
            HeinzelError: If an id, hash, directory or file is unsafe, or its content differs from the checkpoint.
            OSError: If the body is absent, expired or cannot be read.
        """
        identifier(session)
        if re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None:
            msg = "A body checkpoint hash must be a SHA-256 hex digest"
            raise HeinzelError(msg)
        with directory(self.root) as root, child(root, session) as folder:
            return _read_body(folder, turn, expected_hash)

    @contextlib.contextmanager
    def _index(self, root: int) -> Generator[sqlite3.Connection]:
        # Lock order: an optional session lock, then this short global index lock. Reindex takes no session locks.
        with _lock(root, "index.lock", wait=True):
            index = self._connect(root)
            try:
                self._sync(root, index)
                marker = index.execute("SELECT version FROM digest_meta").fetchone()
                if marker is None or marker["version"] != "exact-lines-v2":
                    index.execute("DELETE FROM digest_meta")
                    index.execute("INSERT INTO digest_meta VALUES ('exact-lines-v2')")
                index.commit()
                yield index
                index.commit()
            finally:
                index.close()

    def _connect(self, root: int) -> sqlite3.Connection:
        for _attempt in range(2):
            descriptor = open_file(root, "index.sqlite", os.O_RDWR | os.O_CREAT)
            try:
                before = os.fstat(descriptor)
                root_before = os.fstat(root)
                index = sqlite3.connect(self.root / "index.sqlite", timeout=30)
                after = os.stat("index.sqlite", dir_fd=root, follow_symlinks=False)
                root_after = self.root.stat(follow_symlinks=False)
                if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino) or (
                    root_before.st_dev,
                    root_before.st_ino,
                ) != (root_after.st_dev, root_after.st_ino):
                    index.close()
                    msg = "The session index changed while it was being opened"
                    raise HeinzelError(msg)
            finally:
                os.close(descriptor)
            try:
                index.row_factory = sqlite3.Row
                index.execute("PRAGMA synchronous = FULL")
                had_lines = (
                    index.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'event_lines'"
                    ).fetchone()
                    is not None
                )
                index.executescript(SCHEMA)
                digest_revision = index.execute("SELECT version FROM digest_meta").fetchone()
                if not had_lines or digest_revision is None or digest_revision["version"] != "exact-lines-v2":
                    index.execute("UPDATE sessions SET mtime = -1")
                revision = index.execute("SELECT version FROM meta").fetchone()
                if revision is None or revision["version"] != self.projection_version:
                    # Reproject without discarding the previous checkpoint used to detect changed tails.
                    index.execute("UPDATE sessions SET mtime = -1")
                    index.execute("DELETE FROM meta")
                    index.execute("INSERT INTO meta VALUES (?)", (self.projection_version,))
                    index.commit()
            except sqlite3.DatabaseError:
                index.close()
                # It contains no authoritative record; quarantine damage, then reconstruct from the spines.
                os.rename("index.sqlite", temporary_name(), src_dir_fd=root, dst_dir_fd=root)
                os.fsync(root)
                continue
            return index
        msg = "The session index cannot be rebuilt; check free disk space and directory permissions"
        raise HeinzelError(msg)

    def _sync(self, root: int, index: sqlite3.Connection, *, force: bool = False) -> None:
        known = {str(row["session"]): row for row in index.execute("SELECT * FROM sessions")}
        found: set[str] = set()
        for session in sorted(os.listdir(root)):
            info = os.stat(session, dir_fd=root, follow_symlinks=False)
            if not ID.fullmatch(session) or not stat.S_ISDIR(info.st_mode):
                continue
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != PRIVATE_DIR:
                if session in known:
                    found.add(session)
                    index.execute(
                        "UPDATE sessions SET problem = ?, mtime = -1 WHERE session = ?",
                        ("Session directory is not private or not owned by you", session),
                    )
                continue
            with child(root, session) as folder:
                try:
                    info = os.stat("session.jsonl", dir_fd=folder, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                found.add(session)
                self._refresh(index, session, folder, info, known.get(session), force=force)
        for missing in known.keys() - found:
            index.execute("UPDATE sessions SET problem = ? WHERE session = ?", ("Session spine is missing", missing))

    def _refresh(  # noqa: PLR0913 - one spine and its previous indexed checkpoint
        self,
        index: sqlite3.Connection,
        session: str,
        folder: int,
        info: os.stat_result,
        previous: sqlite3.Row | None,
        *,
        force: bool,
    ) -> None:
        if (
            not force
            and previous
            and not previous["problem"]
            and (previous["size"], previous["mtime"], previous["inode"])
            == (
                info.st_size,
                info.st_mtime_ns,
                info.st_ino,
            )
        ):
            return
        result = _read_spine(folder)
        if previous and int(previous["seq"]) > 0:
            watermark = int(previous["seq"])
            if len(result.events) < watermark or result.hashes[watermark - 1] != previous["hash"]:
                result.problem = "Session differs from its previously indexed checkpoint"
        raw_lines = _raw_lines(folder, result)
        projection: dict[str, JsonValue] = {}
        index.execute("DELETE FROM events WHERE session = ?", (session,))
        index.execute("DELETE FROM event_lines WHERE session = ?", (session,))
        for event, digest, raw in zip(result.events, result.hashes, raw_lines, strict=True):
            projection = PROJECTION.validate_python(self.project(projection, event))
            self._insert(index, session, event)
            self._insert_line(index, session, event.seq, digest, raw)
        if previous and result.problem:
            # Preserve the original checkpoint: a second reindex must not legitimize the damaged tail.
            index.execute(
                "UPDATE sessions SET size = ?, mtime = ?, inode = ?, problem = ? WHERE session = ?",
                (info.st_size, info.st_mtime_ns, info.st_ino, result.problem, session),
            )
        else:
            self._save_head(index, session, result, projection, info)

    @staticmethod
    def _head(row: sqlite3.Row) -> SessionHead:
        return SessionHead(
            session=str(row["session"]),
            seq=int(row["seq"]),
            hash=row["hash"],
            updated=datetime.fromisoformat(row["updated"]) if row["updated"] else None,
            projection=json.loads(row["projection"]),
            problem=row["problem"],
        )

    @staticmethod
    def _insert(index: sqlite3.Connection, session: str, event: SessionEvent) -> None:
        index.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
            (session, event.seq, event.kind, event.turn, event.key, event.model_dump_json()),
        )

    @staticmethod
    def _insert_line(index: sqlite3.Connection, session: str, seq: int, digest: str, raw: bytes) -> None:
        index.execute("INSERT INTO event_lines VALUES (?, ?, ?, ?)", (session, seq, digest, raw))

    @staticmethod
    def _save_head(
        index: sqlite3.Connection,
        session: str,
        result: SessionRead,
        projection: dict[str, JsonValue],
        info: os.stat_result,
    ) -> None:
        last = result.events[-1] if result.events else None
        index.execute(
            "INSERT OR REPLACE INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session,
                last.seq if last else 0,
                result.hashes[-1] if last else None,
                info.st_size,
                info.st_mtime_ns,
                info.st_ino,
                last.ts.isoformat() if last else None,
                json.dumps(projection, allow_nan=False),
                result.problem,
            ),
        )


class SessionWriter:
    """A session lock's scoped writer; it cannot be used after leaving `SessionStore.writer`."""

    def __init__(self, store: SessionStore, root: int, folder: int, session: str) -> None:
        """Bind a writer to the store's already-open directories and session lock."""
        self.store, self.root, self.folder, self.session = store, root, folder, session
        self.active = True

    def _require_active(self) -> None:
        if not self.active:
            msg = "A session writer can only be used inside its writer context"
            raise HeinzelError(msg)

    def checkpoint(self, name: str) -> dict[str, JsonValue] | None:
        """Read a scoped atomic checkpoint while holding this session's writer lock."""
        self._require_active()
        return read_checkpoint(self.folder, name)

    def replace_checkpoint(self, name: str, value: dict[str, JsonValue]) -> None:
        """Durably replace a bounded session checkpoint without appending audit-history events.

        Raises:
            HeinzelError: If the checkpoint name or payload is unsafe or exceeds the storage limit.
            OSError: If the private file cannot be atomically replaced or synced.
        """
        self._require_active()
        payload = PROJECTION.validate_python(value, strict=True)
        replace_checkpoint(self.folder, name, payload)

    def append(
        self,
        kind: str,
        *,
        data: dict[str, JsonValue] | None = None,
        turn: str | None = None,
        key: str | None = None,
    ) -> SessionEvent:
        """Durably append a checkpoint; repeating its kind and key returns the same event.

        Raises:
            HeinzelError: If the session is damaged, the key conflicts, or the envelope is oversized.
            OSError: If storage cannot be written.
            ValidationError: If the envelope is invalid.
        """
        self._require_active()
        payload = PROJECTION.validate_python({} if data is None else data, strict=True)
        with self.store._index(self.root) as index:  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001 - scoped writer
            row = index.execute("SELECT * FROM sessions WHERE session = ?", (self.session,)).fetchone()
            if row and row["problem"]:
                raise HeinzelError(str(row["problem"]))
            event = SessionEvent(
                seq=int(row["seq"]) + 1 if row else 1,
                ts=self.store.clock(),
                prev=row["hash"] if row else None,
                kind=kind,
                turn=turn,
                key=key,
                data=payload,
            )
            if key is not None:
                duplicate = index.execute(
                    "SELECT event FROM events WHERE session = ? AND kind = ? AND key = ?", (self.session, kind, key)
                ).fetchone()
                if duplicate:
                    original = SessionEvent.model_validate_json(duplicate["event"])
                    if encoded(original.data) != encoded(payload) or original.turn != turn:
                        msg = "This checkpoint key already identifies different data; use a new key for a new event"
                        raise HeinzelError(msg)
                    return original
            line = encoded(cast("JsonValue", event.model_dump(mode="json")))
            if len(line) > MAX_LINE:
                msg = "A session checkpoint is too large; put content in a turn body"
                raise HeinzelError(msg)
            projection = PROJECTION.validate_python(
                self.store.project(json.loads(row["projection"]) if row else {}, event)
            )
            append_line(self.folder, "session.jsonl", line)
            self.store._insert(  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001 - scoped writer
                index, self.session, event
            )
            self.store._insert_line(  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001 - scoped writer
                index, self.session, event.seq, hashlib.sha256(line).hexdigest(), line
            )
            info = os.stat("session.jsonl", dir_fd=self.folder, follow_symlinks=False)
            self.store._save_head(  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001 - scoped writer
                index,
                self.session,
                SessionRead(events=[event], hashes=[hashlib.sha256(line).hexdigest()]),
                projection,
                info,
            )
            return event

    def append_body(self, turn: str, value: JsonValue) -> str:
        """Append opaque conversation JSON to a private turn body and return its complete content hash.

        Raises:
            HeinzelError: If the writer, id or file is unsafe.
            OSError: If storage cannot be read or written.
        """
        self._require_active()
        identifier(turn)
        with child(self.folder, "turns", create=True) as folder:
            with contextlib.suppress(FileNotFoundError):
                self.body(turn)
            append_line(folder, f"{turn}.jsonl", encoded(JSON_VALUE.validate_python(value, strict=True)))
            descriptor = open_file(folder, f"{turn}.jsonl", os.O_RDONLY)
            with os.fdopen(descriptor, "rb") as handle:
                return hashlib.file_digest(handle, "sha256").hexdigest()

    def body(self, turn: str, *, expected_hash: str | None = None) -> list[JsonValue]:
        """Read a turn's conversation records; an expired body is an explicit missing file.

        Raises:
            HeinzelError: If the writer, id or file is unsafe, or the body is incomplete.
            OSError: If the body is absent or cannot be read.
        """
        self._require_active()
        return _read_body(self.folder, turn, expected_hash)
