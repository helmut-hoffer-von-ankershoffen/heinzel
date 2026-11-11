"""Private child process for a bounded MCP HTTP check; diagnostics never leave this process."""

import logging
import sys

logging.disable(logging.CRITICAL)  # SDK diagnostics can reflect arbitrary server-controlled headers and bodies.

import anyio  # noqa: E402 - suppress third-party logging before importing the SDK
import httpx2  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402


async def _observe(url: str, token: str) -> int:
    async with (
        httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, trust_env=False) as client,
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
    if len(sys.argv) != 2:  # noqa: PLR2004 - exactly one validated endpoint
        raise SystemExit(2)
    token = sys.stdin.readline(4097).rstrip("\r\n")
    try:
        count = anyio.run(_observe, sys.argv[1], token)
    except Exception:  # noqa: BLE001 - never relay SDK diagnostics or server-controlled bytes
        raise SystemExit(1) from None
    sys.stdout.write(str(count))


if __name__ == "__main__":
    main()
