"""Private continuity binding for the existing pinned direct model API path.

A fingerprint is not an account identity or a grant. The host must keep the snapshot private, bind it to a real
review/decision when required, and enforce runner, price, budget and revision fences independently at launch.
"""

import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Callable
from typing import Any, Never, SupportsIndex

from heinzel import ConfiguredApiAccess, ConfiguredApiLease, ConfiguredApiSnapshot, CurrentUser, HeinzelError
from heinzel.modules.ai.access._pydantic import PydanticAiAccess, build_reserved_openai

MODEL = "openai:gpt-4.1-mini-2025-04-14"

_ENDPOINT = "https://api.openai.com/v1"
_KEY_NAME = "api-access-fingerprint-v1"
_KEY_BYTES = 32
_MAX_CREDENTIAL_BYTES = 16_384


def _private_key(current: CurrentUser) -> bytes:
    """Create/read an owner-private installation key with lock, atomic publish and no symlink/hardlink following.

    Raises:
        HeinzelError: If the private identity or key is unsafe or damaged.
    """
    current.owner()  # creates and verifies the private identity directory
    directory = os.open(current.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        CurrentUser._private(os.fstat(directory), directory=True)  # noqa: SLF001 - same ownership fence as identity
        lock = CurrentUser._open(directory, "owner.lock", os.O_RDWR | os.O_CREAT)  # noqa: SLF001
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                handle = CurrentUser._open(directory, _KEY_NAME, os.O_RDONLY)  # noqa: SLF001
            except FileNotFoundError:
                temporary = f".api-binding-{secrets.token_hex(12)}"
                fd = CurrentUser._open(directory, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)  # noqa: SLF001
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(secrets.token_bytes(_KEY_BYTES))
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.rename(temporary, _KEY_NAME, src_dir_fd=directory, dst_dir_fd=directory)
                    os.fsync(directory)
                finally:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(temporary, dir_fd=directory)
                handle = CurrentUser._open(directory, _KEY_NAME, os.O_RDONLY)  # noqa: SLF001
            with os.fdopen(handle, "rb") as stream:
                key = stream.read(_KEY_BYTES + 1)
            if len(key) != _KEY_BYTES:
                msg = "The private API binding key needs repair"
                raise HeinzelError(msg)
            return key
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)
    finally:
        os.close(directory)


class _ConfiguredApiLease(ConfiguredApiLease):
    """Ephemeral host-only credential lease; never serialize or expose it to a model or API caller."""

    def __init__(self, binding: "ConfiguredApiBinding", snapshot: ConfiguredApiSnapshot, credential: str) -> None:
        self._binding = binding
        self._snapshot = snapshot
        self._credential: str | None = credential

    @property
    def snapshot(self) -> ConfiguredApiSnapshot:
        """The secret-free identity corresponding to this exact captured credential."""
        return self._snapshot

    def __repr__(self) -> str:
        return "<ConfiguredApiLease private>"

    def __reduce_ex__(self, _protocol: SupportsIndex) -> Never:
        msg = "A private credential lease cannot be serialized"
        raise TypeError(msg)

    def discard(self) -> None:
        """Drop the local credential reference without launching or building a provider."""
        self._credential = None

    def build_approved(self, approved: ConfiguredApiSnapshot) -> Any:  # noqa: ANN401 - provider is optional dependency
        """Construct with exactly the credential captured for the approved fingerprint, at most once.

        Raises:
            HeinzelError: If the lease, approved snapshot or current host configuration is invalid.
        """
        invalid = "The configured API binding is unavailable or changed"
        credential, self._credential = self._credential, None
        if credential is None or type(approved) is not ConfiguredApiSnapshot or approved != self.snapshot:
            raise HeinzelError(invalid)
        try:
            self._binding._check_configuration(approved.owner)  # noqa: SLF001 - internal lease boundary
            return self._binding._build(credential)  # noqa: SLF001 - same captured secret, never a second read
        except Exception:  # noqa: BLE001 - provider errors may include credential details
            raise HeinzelError(invalid) from None


class ConfiguredApiBinding(ConfiguredApiAccess):
    """Trusted host-only resolver for the existing pinned direct configured API access."""

    def __init__(
        self, current: CurrentUser, access: PydanticAiAccess, *, build: Callable[[str], Any] = build_reserved_openai
    ) -> None:
        if type(access) is not PydanticAiAccess:
            msg = "The configured API binding is unavailable or changed"
            raise HeinzelError(msg)
        self._current = current
        self._access = access
        self._build = build

    def _check_configuration(self, owner: str) -> None:
        invalid = "The configured API binding is unavailable or changed"
        if (
            self._current.principal() != self._current.owner()
            or self._current.principal().id != owner
            or self._access._binding() != "pydantic-ai"  # noqa: SLF001
        ):
            raise HeinzelError(invalid)
        settings = self._access._current_settings()  # noqa: SLF001
        if (
            settings != self._access._settings  # noqa: SLF001
            or settings.model != MODEL
            or settings.base_url is not None
            or os.environ.get("OPENAI_CUSTOM_HEADERS")
        ):
            raise HeinzelError(invalid)

    def lease(self) -> ConfiguredApiLease:
        """Capture one credential value and its snapshot for a host-only at-most-once provider build.

        Raises:
            HeinzelError: If selected access, settings, credential or current owner cannot be verified.
        """
        invalid = "The configured API binding is unavailable or changed"
        try:
            owner = self._current.principal().id
            self._check_configuration(owner)
            # One read, preserving the credential resolver's declared-store/environment precedence.
            credential, source = self._access._key()  # noqa: SLF001
            if not credential or len(credential.encode()) > _MAX_CREDENTIAL_BYTES or not source:
                raise HeinzelError(invalid)  # noqa: TRY301
            self._check_configuration(owner)
            material = json.dumps(
                ["api-access-v1", owner, source, credential], ensure_ascii=True, separators=(",", ":")
            )
            fingerprint = hmac.new(_private_key(self._current), material.encode(), hashlib.sha256).hexdigest()
            snapshot = ConfiguredApiSnapshot(owner, "pydantic-ai", MODEL, _ENDPOINT, fingerprint)
            return _ConfiguredApiLease(self, snapshot, credential)
        except Exception:  # noqa: BLE001 - credential errors may contain private diagnostics
            raise HeinzelError(invalid) from None

    def capture(self) -> ConfiguredApiSnapshot:
        """Return only a secret-free snapshot; no provider is constructed.

        Raises:
            HeinzelError: If current selected access and credentials cannot be verified.
        """
        lease = self.lease()
        snapshot = lease.snapshot
        lease.discard()
        return snapshot

    def recheck(self, snapshot: ConfiguredApiSnapshot) -> None:
        """Reject changed current configuration or resolved credential; this alone does not build a provider.

        Raises:
            HeinzelError: If current access no longer exactly matches the reviewed snapshot.
        """
        if type(snapshot) is not ConfiguredApiSnapshot or self.capture() != snapshot:
            msg = "The configured API binding changed; review current access again"
            raise HeinzelError(msg)
