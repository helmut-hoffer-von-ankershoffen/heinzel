"""Bounded MCP handshake and empty discovery over an already admitted private bridge."""

import json
import time
from collections.abc import Callable
from socket import socket
from typing import Never, cast

from mcp.types import (
    Implementation,
    InitializeRequestParams,
    InitializeResult,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
    ListToolsResult,
    RequestId,
    ServerCapabilities,
    ToolsCapability,
)
from pydantic import JsonValue, ValidationError

from heinzel import __version__
from heinzel.heads.mcp._ipc_admission import PublishedMcpLease
from heinzel.kernel import HeinzelError
from heinzel.kernel._logging import get_logger, step

CATALOG_PROTOCOL_VERSION = "2025-11-25"
CATALOG_FRAME_LIMIT = 16 * 1024
_SECONDS = 2.0
_REFUSED = "The admitted MCP catalog exchange could not be verified"
log = get_logger("mcp")


def _pairs(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    values = dict(pairs)
    if len(values) != len(pairs):
        raise HeinzelError(_REFUSED)
    return values


def _nonfinite(_: str) -> Never:
    raise HeinzelError(_REFUSED)


class _Frames:
    def __init__(self, channel: socket) -> None:
        self.channel = channel
        self.deadline = time.monotonic() + _SECONDS
        self.buffer = b""

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise HeinzelError(_REFUSED)
        return remaining

    def read(self) -> dict[str, JsonValue]:
        self.remaining()
        while b"\n" not in self.buffer:
            if len(self.buffer) >= CATALOG_FRAME_LIMIT:
                raise HeinzelError(_REFUSED)
            self.channel.settimeout(self.remaining())
            chunk = self.channel.recv(CATALOG_FRAME_LIMIT - len(self.buffer))
            if not chunk:
                raise HeinzelError(_REFUSED)
            self.buffer += chunk
        line, _, self.buffer = self.buffer.partition(b"\n")
        data: object = json.loads(line.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite)
        if not isinstance(data, dict):
            raise HeinzelError(_REFUSED)
        return cast("dict[str, JsonValue]", data)


def _response(id_: RequestId, result: InitializeResult | ListToolsResult) -> bytes:
    response = JSONRPCResponse(
        jsonrpc="2.0", id=id_, result=result.model_dump(mode="json", by_alias=True, exclude_none=True)
    )
    frame = response.model_dump_json(by_alias=True, exclude_none=True).encode() + b"\n"
    if len(frame) > CATALOG_FRAME_LIMIT:
        raise HeinzelError(_REFUSED)
    return frame


def serve_catalog_probe(lease: PublishedMcpLease, reply: Callable[[bytes, float], None]) -> None:
    """Negotiate one MCP version, identify the owned generation and answer one empty list.

    The supervisor supplies the fenced reply function. This exchange provides no invocation path and makes
    no claim about installed client readiness.

    Raises:
        HeinzelError: If the lease or any frame cannot be verified within the exchange budget.
    """
    with step(log, "mcp.ipc.catalog_probe", operation="ipc", step="catalog_probe"):
        try:
            _serve_catalog(lease, reply)
        except (OSError, ValidationError, ValueError, RecursionError):
            raise HeinzelError(_REFUSED) from None


def _serve_catalog(lease: PublishedMcpLease, reply: Callable[[bytes, float], None]) -> None:
    lease.require_active()
    frames = _Frames(lease.peer.connection)
    initialize = JSONRPCRequest.model_validate(frames.read(), strict=True)
    if initialize.method != "initialize":
        raise HeinzelError(_REFUSED)
    params = InitializeRequestParams.model_validate(initialize.params, strict=True)
    if params.protocol_version != CATALOG_PROTOCOL_VERSION:
        raise HeinzelError(_REFUSED)
    generation = lease.current_generation
    if generation != lease.peer.generation:
        raise HeinzelError(_REFUSED)
    result = InitializeResult(
        protocol_version=CATALOG_PROTOCOL_VERSION,
        capabilities=ServerCapabilities(tools=ToolsCapability()),
        server_info=Implementation(name="heinzel", version=__version__),
        meta={"heinzel.local/registration": generation.registration_id, "heinzel.local/generation": generation.number},
    )
    reply(_response(initialize.id, result), frames.deadline)
    notification = frames.read()
    initialized = JSONRPCNotification.model_validate(notification, strict=True)
    if "id" in notification or initialized.method != "notifications/initialized" or initialized.params:
        raise HeinzelError(_REFUSED)
    listing = JSONRPCRequest.model_validate(frames.read(), strict=True)
    if listing.method != "tools/list" or listing.id == initialize.id or listing.params:
        raise HeinzelError(_REFUSED)
    reply(_response(listing.id, ListToolsResult(tools=[])), frames.deadline)
