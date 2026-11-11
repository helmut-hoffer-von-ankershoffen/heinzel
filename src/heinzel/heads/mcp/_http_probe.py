"""Private child process for a bounded MCP HTTP check; diagnostics never leave this process."""

import logging
import ssl
import sys
from functools import partial
from pathlib import Path

logging.disable(logging.CRITICAL)  # SDK diagnostics can reflect arbitrary server-controlled headers and bodies.

import anyio  # noqa: E402 - suppress third-party logging before importing the SDK
import httpx2  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

from heinzel.heads.mcp._http_check import loopback_url  # noqa: E402
from heinzel.heads.mcp._managed import STOP_HEADER, STOP_PATH, client_context  # noqa: E402


async def _observe(url: str, token: str, context: ssl.SSLContext | bool = True, *, stop: bool = False) -> int:
    headers = {STOP_HEADER: token} if stop else {"Authorization": f"Bearer {token}"}
    async with httpx2.AsyncClient(headers=headers, verify=context, trust_env=False, follow_redirects=False) as client:
        if stop:
            response = await client.post(url.removesuffix("/mcp") + STOP_PATH)
            if response.status_code != 204:  # noqa: PLR2004 - the only authenticated shutdown acknowledgment
                msg = "The managed worker did not acknowledge shutdown."
                raise ValueError(msg)
            return 0
        return await _initialize(url, client)


async def _initialize(url: str, client: httpx2.AsyncClient) -> int:
    async with (
        streamable_http_client(url, http_client=client) as (read, write, *_),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        return len(tools.tools)


def main() -> None:
    """Write only a count; any error has a nonzero status and no diagnostics.

    Raises:
        SystemExit: If invocation or the MCP exchange fails.
    """
    url, *options = sys.argv[1:] or [""]
    if options and (len(options) != 2 or options[1] not in {"check", "stop"}):  # noqa: PLR2004 - fixed private invocation
        raise SystemExit(2)
    if not loopback_url(url, "https" if options else "http"):
        raise SystemExit(2)
    token = sys.stdin.readline(4097).rstrip("\r\n")
    try:
        context = client_context(Path.cwd(), options[0]) if options else True
        observe = partial(_observe, stop=bool(options and options[1] == "stop"))
        count = anyio.run(observe, url, token, context)
    except Exception:  # noqa: BLE001 - never relay SDK diagnostics or server-controlled bytes
        raise SystemExit(1) from None
    sys.stdout.write(str(count))


if __name__ == "__main__":
    main()
