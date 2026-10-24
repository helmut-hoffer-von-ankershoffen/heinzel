"""Device tokens: an installed web app (a Dock icon) reopens without a new link from `hz ui` (ADR 0046).

`hz ui` still makes a session token per process and opens the browser with it in the URL fragment. With that session
token, and only with it, the app asks for a device token once (`POST /api/_web/device`) and keeps it in its own
storage; afterwards any `hz ui` on the same fixed port accepts it as `Authorization: Bearer`. Only a SHA-256 of each
token is kept, in the state directory (0700, the file 0600), with a name, when it was made and when it was last used.
`hz ui --devices` lists them, `hz ui --revoke ID` (or `all`) removes them, and the app can forget its own.

A device token is 32 random bytes, so a plain SHA-256 is enough to make the stored file useless to whoever reads it;
lookups compare digests with `hmac.compare_digest`.
"""

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, Field, TypeAdapter, ValidationError

from heinzel.kernel import HeinzelError, clean_text

PREFIX = "hzd_"
"""Device tokens start with this, so they are told apart from session tokens at a glance (and in a secret scan)."""
MAX_DEVICES = 20
NAME_LIMIT = 60
UNUSED_FOR = timedelta(days=30)
"""A device token unused this long stops working, so a forgotten browser doesn't keep access."""
TOUCH_EVERY = timedelta(hours=1)
"""`last_used` is written at most this often, so every request doesn't rewrite the file."""
PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600


class Device(BaseModel):
    """A browser or installed app that may reopen the web app with its own token."""

    id: str = Field(description="Short identifier, for `hz ui --revoke`.")
    name: str = Field(description="What the app called itself, e.g. `Chrome on macOS (installed)`.")
    created: AwareDatetime = Field(description="When it was paired.")
    last_used: AwareDatetime = Field(description="When it was last used, to the hour.")
    sha256: str = Field(description="SHA-256 of the token; the token itself is never stored.")


_DEVICES = TypeAdapter(list[Device])


def digest(token: str) -> str:
    """The SHA-256 of a token, hex."""
    return hashlib.sha256(token.encode()).hexdigest()


class Devices:
    """The paired devices of this machine, in `directory`."""

    def __init__(self, directory: Path, clock: Callable[[], datetime] | None = None) -> None:
        """Keep devices in `directory`/devices.json."""
        self.directory = directory
        self.path = directory / "devices.json"
        self._lock = directory / "devices.lock"
        self._clock = clock or (lambda: datetime.now(UTC))

    def paired(self) -> list[Device]:
        """Every paired device, oldest first; a missing or damaged file is none."""
        try:
            return _DEVICES.validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValidationError):
            return []

    def pair(self, name: str) -> tuple[Device, str]:
        """Make a device token for `name`; the record and the token, which is shown this once.

        Raises:
            HeinzelError: If MAX_DEVICES are paired already.
        """
        token = PREFIX + secrets.token_urlsafe(32)
        now = self._clock()
        label = re.sub(r"\s+", " ", clean_text(name)).strip()[:NAME_LIMIT] or "a browser"
        device = Device(id=secrets.token_hex(4), name=label, created=now, last_used=now, sha256=digest(token))
        with self._locked():
            devices = self.paired()
            if len(devices) >= MAX_DEVICES:
                msg = (
                    f"{MAX_DEVICES} devices are paired already; remove one with `hz ui --revoke ID` (`hz ui --devices`)"
                )
                raise HeinzelError(msg)
            self._write([*devices, device])
        return device, token

    def check(self, token: str) -> Device | None:
        """The device whose token this is, or None; notes when it was used, to the hour."""
        if not token.startswith(PREFIX):
            return None
        presented = digest(token)
        found = next((d for d in self.paired() if hmac.compare_digest(d.sha256, presented)), None)
        if found is None:
            return None
        if self._clock() - found.last_used > UNUSED_FOR:
            self.revoke(found.id)  # unused for a month: pair again with a link from `hz ui`
            return None
        if self._clock() - found.last_used > TOUCH_EVERY:
            self._touch(found.id)
        return found

    def revoke(self, which: str) -> list[Device]:
        """Remove the device `which` (an id), or every one with `all`; the ones removed."""
        with self._locked():
            devices = self.paired()
            gone = [d for d in devices if which in {"all", d.id}]
            if gone:
                self._write([d for d in devices if d not in gone])
        return gone

    def _touch(self, device: str) -> None:
        try:
            with self._locked():
                now = self._clock()
                self._write([d.model_copy(update={"last_used": now}) if d.id == device else d for d in self.paired()])
        except OSError:
            return  # only the hint of when it was used is lost

    def _write(self, devices: list[Device]) -> None:
        partial = self.path.with_name(f".{self.path.name}.{os.getpid()}.{secrets.token_hex(4)}")
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(_DEVICES.dump_python(devices, mode="json"), indent=1) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        partial.replace(self.path)

    @contextmanager
    def _locked(self) -> Generator[None]:
        self.directory.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR)
        self.directory.chmod(PRIVATE_DIR)
        descriptor = os.open(self._lock, os.O_WRONLY | os.O_CREAT | os.O_APPEND, PRIVATE_FILE)
        with os.fdopen(descriptor, "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
