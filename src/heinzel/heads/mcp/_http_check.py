"""Bounded, authenticated readiness observation for the ordinary loopback MCP head."""

import re
from urllib.parse import urlsplit

import anyio
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from heinzel.kernel import HeinzelError
from heinzel.kernel._logging import get_logger, step

log = get_logger("mcp.http.check")

_MAX_PORT = 65535
_TOKEN = re.compile(r"hzmcp_[A-Za-z0-9_-]{1,4090}\Z")


def _loopback_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "http"
        and parts.hostname == "127.0.0.1"
        and port is not None
        and 0 < port <= _MAX_PORT
        and parts.username is None
        and parts.password is None
        and parts.path == "/mcp"
        and not parts.query
        and not parts.fragment
    )


def check_http(url: str, token: str) -> int:
    """Initialize and list tools over authenticated loopback HTTP; return the observed count.

    This observes an endpoint, not ownership of a worker or an installed client configuration.

    Raises:
        HeinzelError: If the endpoint, token, transport or MCP initialization is unavailable.
    """
    with step(log, "mcp.http.check"):
        return _check_http(url, token)


def _check_http(url: str, token: str) -> int:
    if not _loopback_url(url) or not _TOKEN.fullmatch(token):
        msg = "MCP check needs a loopback HTTP /mcp URL and a valid token."
        raise HeinzelError(msg)

    async def observe() -> int:
        with anyio.fail_after(10):
            async with (
                httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, trust_env=False) as client,
                streamable_http_client(url, http_client=client) as (read, write, *_),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                tools = await session.list_tools()
                return len(tools.tools)

    try:
        return anyio.run(observe)
    except Exception:  # noqa: BLE001 - SDK failures must not expose bearer-bearing request details
        msg = "The MCP endpoint could not be authenticated and initialized."
        raise HeinzelError(msg) from None
