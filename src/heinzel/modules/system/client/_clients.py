"""Which desktop app connected to this workspace: flavor, app and backend version, machine and last seen.

The apps present `X-Heinzel-Client: flavor=native;app=1.2;backend=0.9.54;machine=Air` (Electron) or the same text as
`HeinzelClient(...)` in the user agent (the Swift app, whose web view cannot add headers). The facts are not secrets;
a client without them is recorded as `unknown`.
"""

import contextlib
import json
import os
import re
import threading
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, unquote

from pydantic import BaseModel, Field

CLIENTS_FILENAME = "clients.json"
CLIENT_HEADER = "x-heinzel-client"
UNKNOWN = "unknown"
FLAVORS = ("native", "electron")
MAX_CLIENTS = 50
MAX_FIELD = 64
MAX_HEADER = 400
TOUCH_EVERY = timedelta(seconds=30)
_UA = re.compile(r"HeinzelClient\(([^)]{1,400})\)")


class ConnectedClient(BaseModel):
    """One client machine as the workspace last saw it."""

    flavor: str = Field(default=UNKNOWN, description="`native`, `electron` or `unknown` for a client sending nothing.")
    app_version: str = Field(default=UNKNOWN, description="The desktop app's version, or `unknown`.")
    backend_version: str = Field(default=UNKNOWN, description="The backend version the client runs, or `unknown`.")
    machine: str = Field(default=UNKNOWN, description="The client's machine name, or `unknown`.")
    last_seen: datetime = Field(description="When this client last called the workspace.")


def client_header(flavor: str, app_version: str, backend_version: str | None, machine: str) -> str:
    """The header value an app sends; the inverse of `parse_client`."""
    parts = {"flavor": flavor, "app": app_version, "backend": backend_version or UNKNOWN, "machine": quote(machine)}
    return ";".join(f"{key}={value}" for key, value in parts.items())


def parse_client(headers: Mapping[str, str], now: datetime) -> ConnectedClient:
    """Read the client identity from request headers; anything missing or malformed becomes `unknown`."""
    text = headers.get(CLIENT_HEADER)
    if not text and (found := _UA.search(headers.get("user-agent", ""))):
        text = found.group(1)
    values: dict[str, str] = {}
    for part in (text or "")[:MAX_HEADER].split(";"):
        key, _, value = part.partition("=")
        value = unquote(value.strip())
        if value and len(value) <= MAX_FIELD and value.isprintable():
            values[key.strip()] = value
    flavor = values.get("flavor", UNKNOWN)
    return ConnectedClient(
        flavor=flavor if flavor in FLAVORS else UNKNOWN,
        app_version=values.get("app", UNKNOWN),
        backend_version=values.get("backend", UNKNOWN),
        machine=values.get("machine", UNKNOWN),
        last_seen=now,
    )


class ConnectedClients:
    """The clients that connected, kept in `directory`/clients.json so the CLI reads what the web server recorded."""

    def __init__(self, directory: Path) -> None:
        self._path = directory / CLIENTS_FILENAME
        self._lock = threading.Lock()
        self._touched: dict[tuple[str, str], datetime] = {}

    def touch(self, client: ConnectedClient, every: timedelta = TOUCH_EVERY) -> None:
        """Record `client` unless the same client was recorded within `every`: busy pages don't write per request."""
        key = (client.flavor, client.machine)
        last = self._touched.get(key)
        if last is not None and client.last_seen - last < every:
            return
        self._touched[key] = client.last_seen
        self.record(client)

    def record(self, client: ConnectedClient) -> None:
        """Remember `client`, one entry per flavor and machine; a write that fails never fails a request."""
        with self._lock, contextlib.suppress(OSError, ValueError):
            known = {(item.flavor, item.machine): item for item in self.recorded()}
            known[client.flavor, client.machine] = client
            newest = sorted(known.values(), key=lambda item: item.last_seen, reverse=True)[:MAX_CLIENTS]
            self._path.parent.mkdir(parents=True, exist_ok=True)
            partial = self._path.with_suffix(f".{os.getpid()}.tmp")
            partial.write_text(json.dumps([item.model_dump(mode="json") for item in newest]), encoding="utf-8")
            partial.replace(self._path)

    def recorded(self) -> list[ConnectedClient]:
        """The recorded clients, newest first; an unreadable file is an empty list."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            items = [ConnectedClient.model_validate(item) for item in raw]
        except (OSError, ValueError, TypeError):
            return []
        return sorted(items, key=lambda item: item.last_seen, reverse=True)


def now_utc() -> datetime:
    """The current time, in one place."""
    return datetime.now(UTC)
