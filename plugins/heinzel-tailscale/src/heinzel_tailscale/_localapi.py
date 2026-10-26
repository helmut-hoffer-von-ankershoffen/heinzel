"""Conditional Serve updates through the installed client's ordinary-user local API; never a remote host."""

import json
import re
import shlex
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import Never, cast

import httpx

from heinzel import HeinzelError
from heinzel_tailscale._cli import CHUNK, MAX_BYTES, TailscaleCli

LOCAL_PATH = "/localapi/v0/serve-config"
TIMEOUT = 5.0
MAX_CREDENTIAL_BYTES = 4096
TCP_PARTS = 3
SOCKET_PARTS = 4
MAX_ETAG_BYTES = 256
_PORT = re.compile(r"http://localhost:([0-9]{1,5})/localapi/v0/status\Z")


@dataclass(frozen=True, slots=True)
class Connection:
    """A loopback or local-socket endpoint; its ephemeral authentication never enters model output or disk."""

    base: str
    socket: str | None = None
    password: str | None = field(default=None, repr=False)

    @classmethod
    def parse(cls, output: bytes) -> "Connection":
        """Parse the client's fixed credential-discovery format as data, without executing its curl example.

        Raises:
            HeinzelError: If the client reports anything except the known ordinary-user local endpoint forms.
        """
        if len(output) > MAX_CREDENTIAL_BYTES:
            msg = "Tailscale's local endpoint response is too large."
            raise HeinzelError(msg)
        try:
            parts = shlex.split(output.decode("ascii"))
        except (ValueError, UnicodeError) as error:
            msg = "Tailscale reported an unsupported local endpoint."
            raise HeinzelError(msg) from error
        if len(parts) == TCP_PARTS and parts[0] == "curl" and parts[1].startswith("-u:"):
            matched = _PORT.fullmatch(parts[2])
            password = parts[1][3:]
            if matched and 1 <= int(matched[1]) <= 65535 and password and password.isprintable():  # noqa: PLR2004 - TCP port bounds
                return cls(f"http://127.0.0.1:{int(matched[1])}", password=password)
        if (
            len(parts) == SOCKET_PARTS
            and parts[:2] == ["curl", "--unix-socket"]
            and Path(parts[2]).is_absolute()
            and not any(char < " " or char == "\x7f" for char in parts[2])
            and parts[3] == "http://local-tailscaled.sock/localapi/v0/status"
        ):
            return cls("http://local-tailscaled.sock", socket=parts[2])
        msg = "Tailscale did not report a supported local socket; no Serve settings were changed."
        raise HeinzelError(msg)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A complete Serve document and the exact version that conditional updates may replace."""

    config: dict[str, object]
    etag: str


class ServeStore:
    """Read and conditionally change one Serve document, preserving fields the adapter does not own."""

    def __init__(self, cli: TailscaleCli) -> None:
        """Discover a local connection only when an explicitly authorized mutation uses this service."""
        self.cli = cli

    def connect(self) -> Connection:
        """Discover ordinary-user local access through the installed client; never store or log its credential.

        Raises:
            HeinzelError: If the client is unavailable or does not report a supported endpoint.
        """
        result = self.cli.run(("debug", "local-creds"))
        if result.returncode:
            msg = "Tailscale could not provide ordinary-user local access; check its installation and permissions."
            raise HeinzelError(msg)
        return Connection.parse(result.stdout)

    def read(self, connection: Connection, transport: httpx.BaseTransport | None = None) -> Snapshot:
        """Read an exact Serve version for a conditional update.

        Raises:
            HeinzelError: If the local API fails or cannot provide its configuration and version safely.
        """
        body, headers = self._request(connection, "GET", transport=transport)
        versions = headers.get_list("etag")
        if (
            len(versions) != 1
            or not versions[0]
            or len(versions[0]) > MAX_ETAG_BYTES
            or not versions[0].isascii()
            or not versions[0].isprintable()
        ):
            msg = "This Tailscale client cannot provide conditional Serve updates; update it before enabling sharing."
            raise HeinzelError(msg)
        try:
            config = json.loads(body, object_pairs_hook=_unique_fields, parse_constant=_invalid_constant)
        except (ValueError, UnicodeError, RecursionError) as error:
            msg = "Tailscale returned an invalid Serve document; no changes were made."
            raise HeinzelError(msg) from error
        if config is None:
            config = {}
        if not isinstance(config, dict):
            msg = "Tailscale returned an unsupported Serve document; no changes were made."
            raise HeinzelError(msg)
        return Snapshot(cast("dict[str, object]", config), versions[0])

    def write(
        self,
        connection: Connection,
        snapshot: Snapshot,
        updated: dict[str, object],
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Replace only the exact version the validated plan read; a concurrent change is never overwritten.

        Raises:
            HeinzelError: If configuration changed or the outcome cannot be verified from a fresh read.
        """
        self._request(connection, "POST", version=snapshot.etag, body=updated, transport=transport)

    @staticmethod
    def _request(
        connection: Connection,
        method: str,
        *,
        version: str | None = None,
        body: dict[str, object] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> tuple[bytes, httpx.Headers]:
        actual = transport or httpx.HTTPTransport(uds=connection.socket, trust_env=False, retries=0)
        auth = httpx.BasicAuth("", connection.password) if connection.password is not None else None
        headers = {"If-Match": version} if version is not None else {}
        try:
            with (
                httpx.Client(
                    transport=actual,
                    base_url=connection.base,
                    auth=auth,
                    timeout=TIMEOUT,
                    trust_env=False,
                    follow_redirects=False,
                ) as client,
                client.stream(method, LOCAL_PATH, headers=headers, json=body) as response,
            ):
                if response.status_code == HTTPStatus.PRECONDITION_FAILED:
                    msg = "Tailscale Serve changed while this action was planned; nothing was overwritten. Try again."
                    raise HeinzelError(msg)
                if response.status_code != HTTPStatus.OK:
                    msg = "The local Tailscale API refused the action; inspect its current state before trying again."
                    raise HeinzelError(msg)
                data = bytearray()
                for chunk in response.iter_bytes(CHUNK):
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        msg = "The local Tailscale response exceeded its size limit."
                        raise HeinzelError(msg)
                return bytes(data), response.headers
        except httpx.HTTPError as error:
            msg = "The Tailscale action's outcome is unknown; inspect current Serve state before trying again."
            raise HeinzelError(msg) from error


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            msg = "Duplicate JSON field"
            raise ValueError(msg)
        result[key] = value
    return result


def _invalid_constant(_value: str) -> Never:
    msg = "Non-JSON numeric constant"
    raise ValueError(msg)
