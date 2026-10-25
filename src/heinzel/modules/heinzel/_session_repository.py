"""Private per-principal session namespaces and the durable creation reservation catalog."""

import secrets
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime

from pydantic import JsonValue, TypeAdapter

from heinzel import CurrentUser, HeinzelError, SessionHead, SessionRead, SessionStore, SessionWriter
from heinzel.modules.heinzel._session_models import Digest, RequestId, SessionId

_SESSION = TypeAdapter(SessionId)
_REQUEST = TypeAdapter(RequestId)
_DIGEST = TypeAdapter(Digest)
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _identifier(now: datetime) -> str:
    # ULID is a 48-bit UTC millisecond timestamp followed by 80 cryptographically random bits.
    value = (int(now.timestamp() * 1000) << 80) | secrets.randbits(80)
    return "hz-" + "".join(_ALPHABET[(value >> (5 * shift)) & 31] for shift in reversed(range(26)))


def _verified(read: SessionRead) -> SessionRead:
    if read.problem:
        msg = "This session's authoritative journal needs repair before it can be read or changed"
        raise HeinzelError(msg)
    return read


class PersonalSessionRepository:
    """One authenticated owner's separate main, control and creation stores; never rooted in project settings."""

    def __init__(self, current: CurrentUser, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        """Resolve identity for every call, so a cached service cannot retain another request's owner."""
        self.current = current
        self.clock = clock

    @property
    def principal_id(self) -> str:
        """The trusted active principal; unauthenticated calls never fall back to installation ownership."""
        return self.current.principal().id

    def _store(self, namespace: str) -> SessionStore:
        root = self.current.directory.parent / "heinzel-sessions" / self.principal_id / namespace
        return SessionStore(root, clock=self.clock)

    def _catalog(self) -> SessionRead:
        try:
            return _verified(self._store("catalog").read("creations"))
        except FileNotFoundError:
            return SessionRead()

    def _owned(self, session_id: str) -> None:
        _SESSION.validate_python(session_id)
        if not any(
            event.kind == "creation.reserved" and event.data.get("session_id") == session_id
            for event in self._catalog().events
        ):
            msg = "No such personal Heinzel for the authenticated owner"
            raise HeinzelError(msg)

    def reserve(self, request_id: str, payload_hash: str) -> str:
        """Reserve a stable identity before initialization; replay repairs an interrupted creation.

        Raises:
            HeinzelError: If the same request names a different payload or the catalog is damaged.
        """
        _REQUEST.validate_python(request_id)
        _DIGEST.validate_python(payload_hash)
        catalog = self._store("catalog")
        with catalog.writer("creations", wait=True) as writer:
            records = _verified(catalog.read("creations")).events
            prior = next((event for event in records if event.key == request_id), None)
            if prior is not None:
                if prior.kind != "creation.reserved" or prior.data.get("payload_hash") != payload_hash:
                    msg = "This creation request already identifies different Heinzel details"
                    raise HeinzelError(msg)
                return _SESSION.validate_python(prior.data["session_id"])
            session_id = _identifier(self.clock())
            writer.append(
                "creation.reserved", key=request_id, data={"session_id": session_id, "payload_hash": payload_hash}
            )
            return session_id

    def heads(self) -> list[SessionHead]:
        """Only owned agent spines; the separate catalog and inbox roots are never extra sessions."""
        owned = {
            _SESSION.validate_python(event.data.get("session_id"))
            for event in self._catalog().events
            if event.kind == "creation.reserved"
        }
        return [head for head in self._store("main").heads() if head.session in owned]

    def main_read(self, session_id: str) -> SessionRead:
        """Read only after ownership is established; damaged prefixes cannot authorize resumed work."""
        self._owned(session_id)
        return _verified(self._store("main").read(session_id))

    def control_read(self, session_id: str) -> SessionRead:
        """Read accepted controls independently of an active turn's lock."""
        self._owned(session_id)
        return _verified(self._store("control").read(session_id))

    @contextmanager
    def main_writer(self, session_id: str, *, wait: bool = False) -> Generator[SessionWriter]:
        """One long turn writer, acquired before the short control writer if both are needed.

        Yields:
            The owner's active main writer.
        """
        self._owned(session_id)
        with self._store("main").writer(session_id, wait=wait) as writer:
            yield writer

    @contextmanager
    def control_writer(self, session_id: str) -> Generator[SessionWriter]:
        """Wait only for this short control journal, never for a main turn writer.

        Yields:
            The owner's short control writer.
        """
        self._owned(session_id)
        with self._store("control").writer(session_id, wait=True) as writer:
            yield writer

    def body(self, session_id: str, body_id: str, *, expected_hash: str, control: bool = False) -> list[JsonValue]:
        """Read hash-verified immutable private content without locking a running turn."""
        self._owned(session_id)
        return self._store("control" if control else "main").body(session_id, body_id, expected_hash=expected_hash)
