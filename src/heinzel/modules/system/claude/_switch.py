"""Which found MCP servers are switched off, and which the person has been told about (ADR 0146).

A found server is on by default. Switching one off writes a tombstone (its id, name and shape fingerprint) into a
private file; a rescan matches by id first, so a changed argument or token does not switch it back on, and by name
and fingerprint second, so a moved project does not either. The file holds no command, argument, URL, variable value
or header value.

The file is state, not configuration, and its place follows no setting: a project's `hz.toml` or
`hz system config set` (offered to agents over MCP) can change `state_dir`, the daemon is started with that setting
as `HZ_STATE_DIR`, and a file read from an empty folder would switch every server back on. It lives in the platform's
state folder of your home directory, which the CLI, the web app and the daemon share (`switch_file`).
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
SWITCH_ENV = "HZ_CLAUDE_SWITCH_DIR"
"""Moves the folder, for tests and isolated instances. No setting feeds it and the daemon's profile never carries it."""
STATE_FILE = Path("claude") / "servers.json"
LIMIT = 1024 * 1024
PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600


def switch_file() -> Path:
    """Where the switches are kept: the platform's state folder of your home directory.

    Not the `state_dir` setting and not `HZ_STATE_DIR`: configuration files can change the first, and the daemon is
    installed with the first as the second. `HZ_CLAUDE_SWITCH_DIR` moves the folder for a test or an isolated
    instance; a daemon does not inherit it.
    """
    if explicit := os.environ.get(SWITCH_ENV):
        return Path(explicit).expanduser() / STATE_FILE
    # The setting's own default, asked of the field: never a loaded configuration, never the environment.
    default: Path = Settings.model_fields["state_dir"].get_default(call_default_factory=True)
    return default / STATE_FILE


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

    def switch(self, server_id: str, server: FoundServer | None, others: list[FoundServer], *, on: bool) -> None:
        """Switch one entry and nothing else: every one of `others`, the other found servers, stays as it was.

        Switching on removes the entry's own tombstone and those alike whose entries are gone (a moved project);
        switching off writes its own tombstone, also when another entry's already covers it. Whatever that would
        change for another found server is undone for that server: one that was off gets its own tombstone, one that
        was on is allowed past the tombstone that now covers it. `server` is None for a remembered entry that is no
        longer found.
        """
        before = {other.id: self.off(other) for other in others}
        found = {other.id for other in others}
        if server is None:
            self.disabled = [t for t in self.disabled if t.id != server_id]
        elif on:
            self.disabled = [
                t for t in self.disabled if t.id != server.id and not (t.alike(server) and t.id not in found)
            ]
        else:
            self.allowed = [i for i in self.allowed if i != server.id]
            if self.own(server.id) is None:
                self.disabled.append(Tombstone(id=server.id, name=server.name, fingerprint=server.fingerprint))
        for other in others:  # first every tombstone, then the exceptions: a new tombstone can cover a server too
            if before[other.id] and not self.off(other):
                self.disabled.append(Tombstone(id=other.id, name=other.name, fingerprint=other.fingerprint))
        for candidate in (*others, *([server] if server is not None and on else [])):
            stays_on = on if candidate is server else not before[candidate.id]
            if stays_on and self.off(candidate):
                self.allowed.append(candidate.id)


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
