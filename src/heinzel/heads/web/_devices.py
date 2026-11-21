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
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from heinzel.heads.web._state_directory import private_directory
from heinzel.heads.web._widget_fence import FenceState, Receipt, Request, transition
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
HEX_64_PATTERN = r"^[0-9a-f]{64}$"
HEX_32_PATTERN = r"^[0-9a-f]{32}$"
DEVICES_FILENAME = "devices.json"


class Device(BaseModel):
    """A browser or installed app that may reopen the web app with its own token."""

    id: str = Field(description="Short identifier, for `hz ui serve --revoke`.")
    name: str = Field(description="What the app called itself, e.g. `Chrome on macOS (installed)`.")
    created: AwareDatetime = Field(description="When it was paired.")
    last_used: AwareDatetime = Field(description="When it was last used, to the hour.")
    sha256: str = Field(pattern=HEX_64_PATTERN, description="SHA-256 of the token; the token itself is never stored.")
    widget: "WidgetCredential | None" = None
    widget_fence: "WidgetFence | None" = None

    @model_validator(mode="after")
    def validate_fence(self) -> "Device":
        """Reject inconsistent persisted fencing state instead of resetting it.

        Raises:
            ValueError: The stored credential and fence disagree.
        """
        fence = self.widget_fence
        if fence is None:
            return self
        if fence.receipt is None:
            if self.widget is not None or fence.revision != fence.initial_revision:
                msg = "Widget fence has an untracked credential"
                raise ValueError(msg)
            return self
        receipt = fence.receipt
        valid = receipt.revision == fence.revision
        if receipt.operation == "issue":
            valid = valid and self.widget is not None and receipt.commitment == self.widget.sha256
            valid = valid and self.widget is not None and receipt.expires == self.widget.expires
            valid = valid and self.widget is not None and self.widget.owner == fence.owner
            valid = valid and self.widget is not None and self.widget.workspace == fence.workspace
        else:
            valid = valid and self.widget is None and receipt.commitment is None and receipt.expires is None
        if not valid:
            msg = "Inconsistent widget fence"
            raise ValueError(msg)
        return self


class WidgetCredential(BaseModel):
    """A single expiring widget credential belonging to this device."""

    sha256: str = Field(pattern=HEX_64_PATTERN)
    owner: str = Field(min_length=1, max_length=128)
    workspace: str = Field(pattern=HEX_64_PATTERN)
    expires: AwareDatetime


class WidgetReceipt(BaseModel):
    """The last committed fenced operation, with no raw credential."""

    model_config = ConfigDict(extra="forbid")
    attempt: str = Field(pattern=HEX_32_PATTERN)
    operation: Literal["issue", "revoke"]
    previous_revision: str = Field(pattern=HEX_32_PATTERN)
    commitment: str | None = Field(default=None, pattern=HEX_64_PATTERN)
    revision: str = Field(pattern=HEX_32_PATTERN)
    expires: AwareDatetime | None = None


class WidgetFence(BaseModel):
    """Durable pairing incarnation and operation receipt."""

    model_config = ConfigDict(extra="forbid")
    epoch: str = Field(pattern=HEX_32_PATTERN)
    revision: str = Field(pattern=HEX_32_PATTERN)
    initial_revision: str = Field(pattern=HEX_32_PATTERN)
    owner: str = Field(min_length=1, max_length=128)
    workspace: str = Field(pattern=HEX_64_PATTERN)
    receipt: WidgetReceipt | None = None


_DEVICES = TypeAdapter(list[Device])


def digest(token: str) -> str:
    """The SHA-256 of a token, hex."""
    return hashlib.sha256(token.encode()).hexdigest()


class Devices:
    """The paired devices of this machine, in `directory`."""

    def __init__(self, directory: Path, clock: Callable[[], datetime] | None = None) -> None:
        """Keep devices in `directory`/devices.json."""
        self.directory = directory
        self.path = directory / DEVICES_FILENAME
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
        with self._locked() as folder:
            devices = self._read(folder)
            if len(devices) >= MAX_DEVICES:
                msg = (
                    f"{MAX_DEVICES} devices are paired already; remove one with `hz ui serve --revoke ID` "
                    "(`hz ui serve --devices`)"
                )
                raise HeinzelError(msg)
            for _ in range(MAX_DEVICES + 1):
                identifier = secrets.token_hex(4)
                if all(existing.id != identifier for existing in devices):
                    break
            else:
                msg = "Could not allocate a unique device identifier"
                raise HeinzelError(msg)
            device = Device(id=identifier, name=label, created=now, last_used=now, sha256=digest(token))
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

    def widget_issue(self, device_id: str, owner: str, token: str | None = None) -> tuple[str, str, datetime] | None:
        """Replace this device's widget credential; never issue to an unpaired device."""
        with self._locked() as folder:
            devices = self._read(folder)
            device = next((d for d in devices if d.id == device_id), None)
            if token is not None:
                device = self._widget_parent(devices, device_id, token)
            if device is None or self._clock() - device.last_used > UNUSED_FOR:
                return None
            if device.widget_fence is not None or self._fence_marker(folder, device):
                return None
            workspace = self._widget_workspace(folder)
            token = "hzw_" + secrets.token_urlsafe(32)
            expires = self._clock() + timedelta(hours=24)
            credential = WidgetCredential(sha256=digest(token), owner=owner, workspace=workspace, expires=expires)
            self._write(
                folder,
                [
                    d.model_copy(update={"widget": credential})
                    if d.id == device.id and d.sha256 == device.sha256
                    else d
                    for d in devices
                ],
            )
            return token, workspace, expires

    def widget_check(self, token: str, owner: str, workspace: str) -> str | None:
        """Check scope, expiry and live parent pairing on every request.

        Raises:
            HeinzelError: The private fence or workspace identity is damaged.
        """
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
                    marked = self._fence_marker(folder, device)
                    if marked != (device.widget_fence is not None):
                        msg = "The widget fence is damaged"
                        raise HeinzelError(msg)
                    return device.id
        return None

    def widget_revoke(self, device_id: str, token: str | None = None) -> bool:
        """Revoke only this paired device's widget credential."""
        with self._locked() as folder:
            devices = self._read(folder)
            device = next((d for d in devices if d.id == device_id), None)
            if token is not None:
                device = self._widget_parent(devices, device_id, token)
                if device is None:
                    return False
            if device and (device.widget_fence is not None or self._fence_marker(folder, device)):
                return False
            if device:
                self._write(
                    folder,
                    [
                        d.model_copy(update={"widget": None}) if d.id == device.id and d.sha256 == device.sha256 else d
                        for d in devices
                    ],
                )
            return True

    def _fence_marker(self, folder: int, device: Device, *, create: bool = False) -> bool:
        name = "widget-fenced-" + device.sha256
        try:
            descriptor = CurrentUser.open_private(folder, name, os.O_RDONLY)
        except FileNotFoundError:
            if not create:
                return False
            descriptor = CurrentUser.open_private(folder, name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(b"1")
                handle.flush()
                os.fsync(handle.fileno())
            os.fsync(folder)
            return True
        with os.fdopen(descriptor, "rb") as handle:
            CurrentUser.verify_private(os.fstat(handle.fileno()))
            if handle.read(2) != b"1":
                msg = "The widget fence marker is damaged"
                raise HeinzelError(msg)
        return True

    def _widget_parent(self, devices: list[Device], device_id: str, token: str) -> Device | None:
        if not token.startswith(PREFIX):
            return None
        presented = digest(token)
        return next(
            (
                d
                for d in devices
                if d.id == device_id
                and hmac.compare_digest(d.sha256, presented)
                and self._clock() - d.last_used <= UNUSED_FOR
            ),
            None,
        )

    def widget_fence_negotiate(self, device_id: str, token: str, owner: str) -> WidgetFence | None:
        """Bind a durable fence to the exact live pairing; a partial setup fails closed.

        Raises:
            HeinzelError: The stored fence is damaged or belongs to another workspace.
        """
        with self._locked() as folder:
            devices = self._read(folder)
            device = self._widget_parent(devices, device_id, token)
            if device is None:
                return None
            marked = self._fence_marker(folder, device)
            workspace = self._widget_workspace(folder)
            if device.widget_fence is not None:
                if not marked or device.widget_fence.owner != owner or device.widget_fence.workspace != workspace:
                    msg = "The widget fence is damaged"
                    raise HeinzelError(msg)
                os.fsync(folder)
                return device.widget_fence
            if marked:
                msg = "The widget fence is incomplete"
                raise HeinzelError(msg)
            state = FenceState.new()
            fence = WidgetFence(
                epoch=state.epoch,
                revision=state.revision,
                initial_revision=state.revision,
                owner=owner,
                workspace=workspace,
            )
            self._fence_marker(folder, device, create=True)
            self._write(
                folder,
                [
                    d.model_copy(update={"widget": None, "widget_fence": fence})
                    if d.id == device.id and d.sha256 == device.sha256
                    else d
                    for d in devices
                ],
            )
            return fence

    def widget_fence_mutate(
        self, device_id: str, token: str, owner: str, request: Request
    ) -> tuple[WidgetFence, Receipt] | None:
        """Authenticate, compare and commit the operation under one process lock.

        Raises:
            HeinzelError: The stored fence is damaged or belongs to another workspace.
        """
        with self._locked() as folder:
            devices = self._read(folder)
            device = self._widget_parent(devices, device_id, token)
            if device is None:
                return None
            fence = device.widget_fence
            if fence is None or not self._fence_marker(folder, device):
                msg = "The widget fence is unavailable"
                raise HeinzelError(msg)
            if fence.owner != owner or fence.workspace != self._widget_workspace(folder, create=False):
                msg = "The widget fence belongs to another workspace"
                raise HeinzelError(msg)
            receipt = Receipt(**fence.receipt.model_dump()) if fence.receipt else None
            state = FenceState(
                fence.epoch,
                fence.revision,
                device.widget.sha256 if device.widget else None,
                device.widget.expires if device.widget else None,
                receipt,
            )
            result, saved = transition(state, request, self._clock())
            if result == state:
                os.fsync(folder)
                return fence, saved
            updated_fence = WidgetFence(
                epoch=result.epoch,
                revision=result.revision,
                initial_revision=fence.initial_revision,
                owner=owner,
                workspace=fence.workspace,
                receipt=WidgetReceipt.model_validate(asdict(saved)),
            )
            widget = (
                WidgetCredential(
                    sha256=result.child_hash, owner=owner, workspace=fence.workspace, expires=result.child_expires
                )
                if result.child_hash and result.child_expires
                else None
            )
            self._write(
                folder,
                [
                    d.model_copy(update={"widget": widget, "widget_fence": updated_fence})
                    if d.id == device.id and d.sha256 == device.sha256
                    else d
                    for d in devices
                ],
            )
            return updated_fence, saved

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
            descriptor = CurrentUser.open_private(folder, DEVICES_FILENAME, os.O_RDONLY)
        except FileNotFoundError:
            return []
        with os.fdopen(descriptor, "rb") as handle:
            data = handle.read(256 * 1024 + 1)
        if len(data) > 256 * 1024:
            msg = "The paired device store is damaged"
            raise HeinzelError(msg)
        try:
            devices = _DEVICES.validate_json(data, strict=True)
        except ValidationError:
            msg = "The paired device store is damaged"
            raise HeinzelError(msg) from None
        if len({device.id for device in devices}) != len(devices) or len({device.sha256 for device in devices}) != len(
            devices
        ):
            msg = "The paired device store has ambiguous identities"
            raise HeinzelError(msg)
        return devices

    def _write(self, folder: int, devices: list[Device]) -> None:
        partial = f".devices-{secrets.token_hex(12)}"
        descriptor = CurrentUser.open_private(folder, partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(_DEVICES.dump_python(devices, mode="json"), indent=1) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(partial, DEVICES_FILENAME, src_dir_fd=folder, dst_dir_fd=folder)
            os.fsync(folder)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(partial, dir_fd=folder)

    @contextmanager
    def _folder(self) -> Generator[int]:
        with private_directory(self.directory) as folder:
            yield folder

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
