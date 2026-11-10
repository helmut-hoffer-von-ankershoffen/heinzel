"""Bounded, authenticated readiness observation for the ordinary loopback MCP head."""

import os
import re
import subprocess
import sys
from urllib.parse import urlsplit

from heinzel.kernel import HeinzelError
from heinzel.kernel._logging import get_logger, step

log = get_logger("mcp.http.check")

_MAX_PORT = 65535
_MAX_COUNT_DIGITS = 9
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_TOKEN = re.compile(r"hzmcp_[A-Za-z0-9_-]{1,4090}\Z")


def _loopback_url(url: str) -> bool:
    if _CONTROL.search(url):
        return False
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

    try:
        # The SDK can log attacker-controlled response data, including reflected bearer values. Its
        # diagnostics stay inside a short-lived child; neither output stream is forwarded to our logs.
        result = subprocess.run(  # noqa: S603 - fixed interpreter/module and validated loopback URL
            [sys.executable, "-I", "-m", "heinzel.heads.mcp._http_probe", url],
            input=token,
            env={"PATH": os.defpath},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        if (
            result.returncode == 0
            and result.stdout.isascii()
            and result.stdout.isdecimal()
            and len(result.stdout) <= _MAX_COUNT_DIGITS
        ):
            return int(result.stdout)
    except (OSError, subprocess.TimeoutExpired):
        pass
    msg = "The MCP endpoint could not be authenticated and initialized."
    raise HeinzelError(msg)
