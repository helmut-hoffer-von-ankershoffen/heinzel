"""Trusted installation identity and per-call authentication context (ADR 0068).

Identity never implies a human decision. Heads establish context from local execution or verified credentials;
model inputs and project configuration cannot select a principal. Context is reset even when a call fails.
"""

import contextlib
import fcntl
import os
import re
import secrets
import stat
from collections.abc import Generator
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from heinzel.kernel._errors import HeinzelError

PRIVATE_DIRECTORY = 0o700
PRIVATE_FILE = 0o600
OWNER = re.compile(r"u-[0-9a-f]{32}\Z")
IDENTITY_LIMIT = 128
PRIVATE_READ = "private:read"
PRIVATE_WRITE = "private:write"
LOCAL_PERMISSIONS = frozenset({PRIVATE_READ, PRIVATE_WRITE})
LOCAL_READ_PERMISSIONS = frozenset({PRIVATE_READ})
Authentication = Literal["cli", "mcp-stdio", "daemon", "api-bearer", "web-session", "web-device"]


@dataclass(frozen=True, slots=True)
class Principal:
    """One opaque installation owner; not a profile, credential or human-approval assertion."""

    id: str

    def __post_init__(self) -> None:
        """Refuse an identity that is not an opaque owner id.

        Raises:
            ValueError: If the id is malformed.
        """
        if not OWNER.fullmatch(self.id):
            msg = "A principal id must be an opaque installation owner id"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class CallIdentity:
    """Verified identity and credential provenance for one call; no approval-issuance authority."""

    principal: Principal
    authentication: Authentication
    credential_id: str
    audience: str
    permissions: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        """Copy scopes so trusted callers cannot mutate a context after it was established."""
        object.__setattr__(self, "permissions", frozenset(self.permissions))


class CurrentUser:
    """The trusted principal for this call, with a private stable local owner read only when needed."""

    def __init__(self, directory: Path) -> None:
        """Keep the owner under the user's trusted state directory; perform no I/O yet."""
        self.directory = directory
        self._context: ContextVar[CallIdentity | None] = ContextVar("hz_call_identity", default=None)

    def context(self) -> CallIdentity:
        """The verified call context; unauthenticated calls never fall back to the local owner.

        Raises:
            HeinzelError: If no trusted head authenticated this call.
        """
        context = self._context.get()
        if context is None:
            msg = "This operation requires an authenticated private API, local CLI or stdio MCP connection"
            raise HeinzelError(msg)
        return context

    def principal(self) -> Principal:
        """The authenticated owner of this call.

        Raises:
            HeinzelError: If no trusted head authenticated this call.
        """
        return self.context().principal

    @contextlib.contextmanager
    def using(self, identity: CallIdentity | None) -> Generator[None]:
        """Scope a trusted head's immutable identity; None explicitly clears inherited context.

        Yields:
            Nothing; the context is restored when the head's call completes.
        """
        token = self._context.set(identity)
        try:
            yield
        finally:
            self._context.reset(token)

    @contextlib.contextmanager
    def local(self, authentication: Literal["cli", "mcp-stdio", "daemon"]) -> Generator[None]:
        """Authenticate trusted local head/daemon startup as the owner, without asserting human approval.

        Yields:
            Nothing; nested calls share the local context.

        Raises:
            HeinzelError: If local identity storage is unsafe or unreadable.
        """
        identity = CallIdentity(self.owner(), authentication, "local", "hz-local", LOCAL_PERMISSIONS)
        with self.using(identity):
            yield

    @contextlib.contextmanager
    def local_read(self, authentication: Literal["cli", "mcp-stdio", "daemon"]) -> Generator[None]:
        """Authenticate a trusted local read as the owner, without granting private writes.

        Raises:
            HeinzelError: If local identity storage is unsafe or unreadable.
        """
        identity = CallIdentity(self.owner(), authentication, "local", "hz-local", LOCAL_READ_PERMISSIONS)
        with self.using(identity):
            yield

    def owner(self) -> Principal:
        """Read or atomically initialize the private installation identity, under a cross-process lock.

        Raises:
            HeinzelError: If identity files have unsafe links, permissions or ownership, or cannot be read.
        """
        try:
            self.directory.mkdir(mode=PRIVATE_DIRECTORY, parents=True, exist_ok=True)
            parent = os.open(self.directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(parent)  # also persist the new identity-directory entry before publishing its owner
            finally:
                os.close(parent)
            folder = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                self._private(os.fstat(folder), directory=True)
                lock = self._open(folder, "owner.lock", os.O_RDWR | os.O_CREAT)
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    return self._owner(folder)
                finally:
                    os.close(lock)
            finally:
                os.close(folder)
        except OSError:
            msg = "The installation identity cannot be opened safely; check its private state directory"
            raise HeinzelError(msg) from None

    @staticmethod
    def _private(info: os.stat_result, *, directory: bool = False) -> None:
        expected = PRIVATE_DIRECTORY if directory else PRIVATE_FILE
        kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if (
            not kind
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != expected
            or (not directory and info.st_nlink != 1)
        ):
            msg = "Installation identity storage must be private, owned by you and free of file links"
            raise HeinzelError(msg)

    @classmethod
    def _open(cls, folder: int, name: str, flags: int) -> int:
        protected = flags | os.O_NOFOLLOW | os.O_NONBLOCK
        if flags & os.O_CREAT and not flags & os.O_EXCL:
            try:
                descriptor = os.open(name, protected | os.O_EXCL, PRIVATE_FILE, dir_fd=folder)
            except FileExistsError:
                descriptor = os.open(name, protected & ~os.O_CREAT, PRIVATE_FILE, dir_fd=folder)
        else:
            descriptor = os.open(name, protected, PRIVATE_FILE, dir_fd=folder)
        try:
            cls._private(os.fstat(descriptor))
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    @classmethod
    def _owner(cls, folder: int) -> Principal:
        try:
            descriptor = cls._open(folder, "owner-v1", os.O_RDONLY)
        except FileNotFoundError:
            # Publish only complete durable bytes. A crash before rename leaves no partial owner identity.
            temporary = f".owner-{secrets.token_hex(12)}"
            descriptor = cls._open(folder, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(f"u-{secrets.token_hex(16)}\n".encode("ascii"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.rename(temporary, "owner-v1", src_dir_fd=folder, dst_dir_fd=folder)
                os.fsync(folder)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(temporary, dir_fd=folder)
            descriptor = cls._open(folder, "owner-v1", os.O_RDONLY)
        with os.fdopen(descriptor, "rb") as handle:
            data = handle.read(IDENTITY_LIMIT + 1)
        try:
            text = data.decode("ascii")
        except UnicodeDecodeError:
            text = ""
        if len(data) > IDENTITY_LIMIT or not text.endswith("\n") or not OWNER.fullmatch(text[:-1]):
            msg = "The installation identity is damaged; restore its private owner file before using private data"
            raise HeinzelError(msg)
        return Principal(text[:-1])
