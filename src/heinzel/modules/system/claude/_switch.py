"""Which found MCP servers are switched off, and which the person has been told about (ADR 0146).

A found server is on by default. Switching one off writes a tombstone (its id, name and shape fingerprint) into a
private file; a rescan matches by id first, so a changed argument or token does not switch it back on, and by name
and fingerprint second, so a moved project does not either. The file holds no command, argument, URL, variable value
or header value.

The file is state, not configuration, and its place does not follow the `state_dir` setting: a project's `hz.toml`
or `hz system config set` (offered to agents over MCP) can change that setting, and pointing it at an empty folder
would switch every server back on. Only the `HZ_STATE_DIR` environment variable or the platform's default decides
where it is (`switch_file`).
"""

import fcntl
import json
import os
import secrets
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from heinzel import HeinzelError, Settings
from heinzel.modules.system.claude._models import FoundServer

VERSION = 1
STATE_ENV = "HZ_STATE_DIR"
STATE_FILE = Path("claude") / "servers.json"
LIMIT = 1024 * 1024
PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600


def switch_file() -> Path:
    """Where the switches are kept: under `HZ_STATE_DIR` when the environment sets it, else the platform's default.

    Never the `state_dir` setting, which configuration files can change.
    """
    explicit = os.environ.get(STATE_ENV)
    # The setting's own default (the platform's state folder), asked of the field, never of a loaded configuration.
    default: Path = Settings.model_fields["state_dir"].get_default(call_default_factory=True)
    root = Path(explicit).expanduser() if explicit else default
    return root / STATE_FILE


class Tombstone(BaseModel):
    """A server the person switched off: enough to recognise it again, nothing that connects to it."""

    id: str = Field(max_length=4096)
    name: str = Field(max_length=1024)
    fingerprint: str = Field(max_length=128)

    def alike(self, server: FoundServer) -> bool:
        """An entry of the same name and shape, wherever it was found."""
        return self.name == server.name and self.fingerprint == server.fingerprint


class SwitchState(BaseModel):
    """The file's content."""

    version: int = VERSION
    disabled: list[Tombstone] = Field(default_factory=list[Tombstone])
    allowed: list[str] = Field(
        default_factory=list[str],
        description="Ids switched back on while another entry of the same name and shape stays switched off.",
    )
    acknowledged: list[str] = Field(default_factory=list[str], description="Ids the first-run notice was closed for.")

    def own(self, server_id: str) -> Tombstone | None:
        """The tombstone written for exactly this id."""
        return next((tombstone for tombstone in self.disabled if tombstone.id == server_id), None)

    def off(self, server: FoundServer) -> bool:
        """Whether a tombstone keeps `server` off: its own, or one alike that it was not allowed past."""
        if self.own(server.id) is not None:
            return True
        return server.id not in self.allowed and any(tombstone.alike(server) for tombstone in self.disabled)

    def switch_off(self, server: FoundServer) -> None:
        """Give `server` its own tombstone, also when another entry's already covers it."""
        self.allowed = [i for i in self.allowed if i != server.id]
        if self.own(server.id) is None:
            self.disabled.append(Tombstone(id=server.id, name=server.name, fingerprint=server.fingerprint))

    def switch_on(self, server: FoundServer, others: set[str]) -> None:
        """Remove what keeps `server` off without switching on any of `others`, the ids of the other found servers.

        Its own tombstone goes, and so do tombstones alike whose entries are gone (a moved project). One that belongs
        to another found server stays, and `server` is allowed past it.
        """
        self.disabled = [t for t in self.disabled if t.id != server.id and not (t.alike(server) and t.id not in others)]
        if any(t.alike(server) for t in self.disabled) and server.id not in self.allowed:
            self.allowed.append(server.id)


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
            self.path.parent.chmod(PRIVATE_DIR)
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
        text = state.model_dump_json(indent=2) + "\n"
        if len(text.encode()) > LIMIT:  # never write a file `read` would refuse: that would switch everything off
            msg = (
                f"{self.path} would grow beyond {LIMIT} bytes. Nothing was changed; switch servers you no longer "
                "have back on with `hz system claude enable <id>` to make room."
            )
            raise HeinzelError(msg)
        partial = self.path.with_name(f".{self.path.name}.{os.getpid()}.{secrets.token_hex(4)}")
        try:
            descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, PRIVATE_FILE)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            partial.replace(self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
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
