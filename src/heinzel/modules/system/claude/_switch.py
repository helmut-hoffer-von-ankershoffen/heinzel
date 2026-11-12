"""Which found MCP servers are switched off, and which the person has been told about (ADR 0146).

A found server is on by default. Switching one off writes a tombstone (its id, name and shape fingerprint) into a
private file in hz's state folder; a rescan matches by id first, so a changed argument or token does not switch it
back on, and by name and fingerprint second, so a moved project does not either. The file holds no command, argument,
URL, variable value or header value. It is state, not configuration, on purpose: `hz system config set` is offered
to agents over MCP, and no operation an agent can call writes this file.
"""

import fcntl
import json
import os
import secrets
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from heinzel import HeinzelError
from heinzel.modules.system.claude._models import FoundServer

VERSION = 1
LIMIT = 1024 * 1024
PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600


class Tombstone(BaseModel):
    """A server the person switched off: enough to recognise it again, nothing that connects to it."""

    id: str = Field(max_length=4096)
    name: str = Field(max_length=1024)
    fingerprint: str = Field(max_length=128)

    def matches(self, server: FoundServer) -> bool:
        """The same entry (id), or one of the same name and shape found in another place."""
        return self.id == server.id or (self.name == server.name and self.fingerprint == server.fingerprint)


class SwitchState(BaseModel):
    """The file's content."""

    version: int = VERSION
    disabled: list[Tombstone] = Field(default_factory=list[Tombstone])
    acknowledged: list[str] = Field(default_factory=list[str], description="Ids the first-run notice was closed for.")

    def off(self, server: FoundServer) -> bool:
        """Whether a tombstone keeps `server` switched off."""
        return any(tombstone.matches(server) for tombstone in self.disabled)


class SwitchStore:
    """Reads and writes the state file; a file it cannot trust reads as `None`, which switches every server off."""

    def __init__(self, path: Path) -> None:
        """Keep the state in `path`, e.g. `<state_dir>/claude/servers.json`."""
        self.path = path

    def read(self) -> SwitchState | None:
        """The state, an empty one when nothing was ever switched, or None when the file cannot be trusted."""
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return SwitchState()
        except OSError:
            return None
        try:
            with os.fdopen(descriptor, "rb") as handle:
                raw = handle.read(LIMIT + 1)
        except OSError:
            return None
        if len(raw) > LIMIT:
            return None
        try:
            state = SwitchState.model_validate(json.loads(raw))
        except (ValueError, ValidationError):
            return None
        return state if state.version == VERSION else None

    @contextmanager
    def change(self) -> Generator[SwitchState]:
        """Read the state under a lock, let the caller change it, then replace the file atomically.

        Raises:
            HeinzelError: If the file cannot be trusted or written; nothing is changed then.

        Yields:
            The state to change in place.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR)
            lock = os.open(
                self.path.with_name(f".{self.path.name}.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, PRIVATE_FILE
            )
        except OSError as exc:
            msg = f"Cannot write {self.path.parent}: {exc.strerror}. Nothing was changed."
            raise HeinzelError(msg) from exc
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = self.read()
            if state is None:
                msg = (
                    f"{self.path} cannot be read, so every server stays switched off. Move the file away to start "
                    "again with every found server on, then switch off the ones you don't want."
                )
                raise HeinzelError(msg)
            yield state
            self._write(state)
        finally:
            os.close(lock)

    def _write(self, state: SwitchState) -> None:
        partial = self.path.with_name(f".{self.path.name}.{os.getpid()}.{secrets.token_hex(4)}")
        try:
            descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, PRIVATE_FILE)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(state.model_dump_json(indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            partial.replace(self.path)
        except OSError as exc:
            partial.unlink(missing_ok=True)
            msg = f"Cannot write {self.path}: {exc.strerror}. Nothing was changed."
            raise HeinzelError(msg) from exc


def apply(servers: Iterable[FoundServer], state: SwitchState | None) -> list[FoundServer]:
    """`servers` with `enabled` set: on unless it is hz itself, a tombstone matches, or the state is untrusted."""
    return [
        server.model_copy(update={"enabled": server.status != "self" and state is not None and not state.off(server)})
        for server in servers
    ]
