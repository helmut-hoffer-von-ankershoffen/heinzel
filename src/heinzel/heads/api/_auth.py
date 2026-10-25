"""Expiring private-API bearer credentials, scoped to one server audience (ADR 0068).

A bearer authenticates access to private operations; it is never proof of a human decision. The issuing server
keeps its credential in memory and writes the one-time client copy only to an owner-only file, never to a URL.
"""

import contextlib
import hmac
import json
import os
import secrets
import stat
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from heinzel.kernel import HeinzelError
from heinzel.kernel._identity import LOCAL_PERMISSIONS, CallIdentity, Principal

DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
LIFETIME = timedelta(hours=12)


@dataclass(frozen=True, slots=True)
class ApiBearer:
    """One short-lived credential whose provenance the server, not a request, supplies."""

    principal: Principal
    audience: str
    credential_id: str
    secret: str = field(repr=False)
    expires_at: datetime
    permissions: frozenset[str] = frozenset()

    @classmethod
    def issue(cls, principal: Principal, audience: str, *, now: datetime | None = None) -> "ApiBearer":
        """Issue an in-memory server credential; its secret is returned only to the trusted starter."""
        return cls(
            principal,
            audience,
            secrets.token_hex(8),
            f"hzapi_{secrets.token_urlsafe(32)}",
            (now or datetime.now(UTC)) + LIFETIME,
            LOCAL_PERMISSIONS,
        )

    def authenticate(self, header: str, audience: str, *, now: datetime) -> CallIdentity | None:
        """Verify this exact audience, expiry and bearer before establishing call context."""
        if audience != self.audience or now >= self.expires_at:
            return None
        if not self.secret or not hmac.compare_digest(header.encode(), f"Bearer {self.secret}".encode()):
            return None
        return CallIdentity(self.principal, "api-bearer", self.credential_id, self.audience, self.permissions)

    def write(self, path: Path) -> None:
        """Write the client copy once to a private file without replacing or following any existing target.

        Raises:
            HeinzelError: If the destination is unsafe, already exists or cannot be written.
        """
        try:
            path.parent.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
            folder = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                info = os.fstat(folder)
                if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != DIRECTORY_MODE:
                    msg = "Private API credential directories must be owner-only and owned by you"
                    raise HeinzelError(msg)
                self._publish(folder, path.name)
                os.fsync(folder)
            finally:
                os.close(folder)
        except OSError:
            msg = "The private API credential file cannot be written safely; choose a new owner-only destination"
            raise HeinzelError(msg) from None

    def _publish(self, folder: int, name: str) -> None:
        temporary = f".credential-{secrets.token_hex(12)}"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, FILE_MODE, dir_fd=folder)
        try:
            payload = {
                "audience": self.audience,
                "credential_id": self.credential_id,
                "expires_at": self.expires_at.isoformat(),
                "token": self.secret,
            }
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            # A same-directory link publishes complete bytes and fails if any target already exists.
            os.link(temporary, name, src_dir_fd=folder, dst_dir_fd=folder, follow_symlinks=False)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=folder)
