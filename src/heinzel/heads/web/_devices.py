"""Device tokens: an installed web app (a Dock icon) reopens without a new link from `hz ui serve` (ADR 0046).

`hz ui serve` still makes a session token per process and opens the browser with it in the URL fragment. With that
session
token, and only with it, the app asks for a device token once (`POST /api/_web/device`) and keeps it in its own
storage; afterwards any `hz ui serve` on the same fixed port accepts it as `Authorization: Bearer`. Only a SHA-256 of
each
token is kept, in the state directory (0700, the file 0600), with a name, when it was made and when it was last used.
`hz ui serve --devices` lists them, `hz ui serve --revoke ID` (or `all`) removes them, and the app can forget its
own.

A device token is 32 random bytes, so a plain SHA-256 is enough to make the stored file useless to whoever reads it;
lookups compare digests with `hmac.compare_digest`.
"""

import contextlib
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
from heinzel.kernel._identity import CurrentUser

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

    id: str = Field(description="Short identifier, for `hz ui serve --revoke`.")
    name: str = Field(description="What the app called itself, e.g. `Chrome on macOS (installed)`.")
    created: AwareDatetime = Field(description="When it was paired.")
    last_used: AwareDatetime = Field(description="When it was last used, to the hour.")
    sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$", description="SHA-256 of the token; the token itself is never stored."
    )
    widget: "WidgetCredential | None" = None


class WidgetCredential(BaseModel):
    """A single expiring widget credential belonging to this device."""

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner: str = Field(min_length=1, max_length=128)
    workspace: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires: AwareDatetime


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
            with self._folder() as folder:
                return self._read(folder)
        except (OSError, HeinzelError):
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
        with self._locked() as folder:
            devices = self._read(folder)
            if len(devices) >= MAX_DEVICES:
                msg = (
                    f"{MAX_DEVICES} devices are paired already; remove one with `hz ui serve --revoke ID` "
                    "(`hz ui serve --devices`)"
                )
                raise HeinzelError(msg)
            self._write(folder, [*devices, device])
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
            self.revoke(found.id)  # unused for a month: pair again with a link from `hz ui serve`
            return None
        if self._clock() - found.last_used > TOUCH_EVERY:
            self._touch(found.id)
        return found

    def widget_issue(self, device_id: str, owner: str) -> tuple[str, str, datetime] | None:
        """Replace this device's widget credential; never issue to an unpaired device."""
        with self._locked() as folder:
            devices = self._read(folder)
            device = next((d for d in devices if d.id == device_id), None)
            if device is None or self._clock() - device.last_used > UNUSED_FOR:
                return None
            workspace = self._widget_workspace(folder)
            token = "hzw_" + secrets.token_urlsafe(32)
            expires = self._clock() + timedelta(hours=24)
            credential = WidgetCredential(sha256=digest(token), owner=owner, workspace=workspace, expires=expires)
            self._write(
                folder, [d.model_copy(update={"widget": credential}) if d.id == device_id else d for d in devices]
            )
            return token, workspace, expires

    def widget_check(self, token: str, owner: str, workspace: str) -> str | None:
        """Check scope, expiry and live parent pairing on every request."""
        if not token.startswith("hzw_") or not re.fullmatch(r"[0-9a-f]{64}", workspace):
            return None
        with self._locked() as folder:
            if not hmac.compare_digest(self._widget_workspace(folder, create=False), workspace):
                return None
            now = self._clock()
            for device in self._read(folder):
                widget = device.widget
                if (
                    widget
                    and widget.owner == owner
                    and widget.workspace == workspace
                    and now < widget.expires
                    and now - device.last_used <= UNUSED_FOR
                    and hmac.compare_digest(widget.sha256, digest(token))
                ):
                    return device.id
        return None

    def widget_revoke(self, device_id: str) -> None:
        """Revoke only this paired device's widget credential."""
        with self._locked() as folder:
            devices = self._read(folder)
            if any(d.id == device_id for d in devices):
                self._write(
                    folder, [d.model_copy(update={"widget": None}) if d.id == device_id else d for d in devices]
                )

    def _widget_workspace(self, folder: int, *, create: bool = True) -> str:
        try:
            descriptor = CurrentUser.open_private(folder, "widget-workspace", os.O_RDONLY)
        except FileNotFoundError:
            if not create:
                return ""
            value = secrets.token_hex(32)
            descriptor = CurrentUser.open_private(folder, "widget-workspace", os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.fsync(folder)
        else:
            with os.fdopen(descriptor, "r", encoding="ascii") as handle:
                CurrentUser.verify_private(os.fstat(handle.fileno()))
                value = handle.read(65)
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            msg = "The widget workspace identity is damaged"
            raise HeinzelError(msg)
        return value

    def revoke(self, which: str) -> list[Device]:
        """Remove the device `which` (an id), or every one with `all`; the ones removed."""
        with self._locked() as folder:
            devices = self._read(folder)
            gone = [d for d in devices if which in {"all", d.id}]
            if gone:
                self._write(folder, [d for d in devices if d not in gone])
        return gone

    def _touch(self, device: str) -> None:
        try:
            with self._locked() as folder:
                now = self._clock()
                self._write(
                    folder,
                    [d.model_copy(update={"last_used": now}) if d.id == device else d for d in self._read(folder)],
                )
        except (OSError, HeinzelError):
            return  # only the hint of when it was used is lost

    def _read(self, folder: int) -> list[Device]:
        try:
            descriptor = CurrentUser.open_private(folder, "devices.json", os.O_RDONLY)
        except FileNotFoundError:
            return []
        with os.fdopen(descriptor, "rb") as handle:
            data = handle.read(256 * 1024 + 1)
        if len(data) > 256 * 1024:
            msg = "The paired device store is damaged"
            raise HeinzelError(msg)
        try:
            return _DEVICES.validate_json(data)
        except ValidationError:
            msg = "The paired device store is damaged"
            raise HeinzelError(msg) from None

    def _write(self, folder: int, devices: list[Device]) -> None:
        partial = f".devices-{secrets.token_hex(12)}"
        descriptor = CurrentUser.open_private(folder, partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(_DEVICES.dump_python(devices, mode="json"), indent=1) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(partial, "devices.json", src_dir_fd=folder, dst_dir_fd=folder)
            os.fsync(folder)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(partial, dir_fd=folder)

    @contextmanager
    def _folder(self) -> Generator[int]:
        self.directory.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR)
        folder = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            CurrentUser.verify_private(os.fstat(folder), directory=True)
            yield folder
        finally:
            os.close(folder)

    @contextmanager
    def _locked(self) -> Generator[int]:
        with self._folder() as folder:
            descriptor = CurrentUser.open_private(folder, "devices.lock", os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            with os.fdopen(descriptor, "a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                try:
                    yield folder
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)
