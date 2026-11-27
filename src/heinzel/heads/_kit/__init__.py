"""The head kit: what several heads share, so no head imports another for it. First: the one Host and Origin policy.

The REST and web heads both apply it, and the web head answers its own routes without importing the REST head.
Readers look `LOOPBACK` up on this module at call time, so a test widens it here, once.
"""

from starlette.datastructures import Headers

FORBIDDEN = 403
DEFAULT_PORTS = frozenset({80, 443})
LOOPBACK = frozenset({"localhost", "127.0.0.1", "[::1]"})
"""Hosts the REST head always answers; `[hz] api_allowed_hosts` adds others."""
API_HOST_HELP = "If you serve on another interface, add its name to [hz] api_allowed_hosts in your user configuration."
WEB_HOST_HELP = (
    "For a private HTTPS proxy, add its exact HTTPS origin to [hz] ui_allowed_origins in your user configuration "
    "and restart hz ui. api_allowed_hosts configures the REST head only."
)


def request_refusal(
    headers: Headers,
    hosts: frozenset[str],
    port: int | None,
    *,
    allowed_origins: frozenset[str] = frozenset(),
    host_help: str = API_HOST_HELP,
) -> str | None:
    """Apply one authority policy to HTTP and WebSocket headers, refusing ambiguous duplicate headers."""
    if len(headers.getlist("host")) != 1 or any(
        len(headers.getlist(name)) > 1 for name in ("origin", "sec-fetch-site")
    ):
        return "Refused: supply exactly one Host and at most one Origin and Sec-Fetch-Site header."
    return refusal(
        headers.get("host", ""),
        headers.get("origin"),
        hosts,
        port,
        headers.get("sec-fetch-site"),
        allowed_origins=allowed_origins,
        host_help=host_help,
    )


def refusal(  # noqa: PLR0913 - the caller headers and the explicit server authority policy
    host: str,
    origin: str | None,
    hosts: frozenset[str],
    port: int | None,
    fetch_site: str | None = None,
    *,
    allowed_origins: frozenset[str] = frozenset(),
    host_help: str = API_HOST_HELP,
) -> str | None:
    """Why a request is refused, or None: its Host must be an allowed name on our port, its Origin our own.

    Without an Origin, a browser's `Sec-Fetch-Site: cross-site` or `same-site` still marks a request another page
    made. On port 80 or 443 a Host without a port is the same port.
    """
    if any(char.isspace() or char in "\\/@?#," or char < " " or char == "\x7f" for char in host):
        return "Refused: malformed Host authority."
    if fetch_site is not None and fetch_site.strip().lower() not in {"none", "same-origin"}:
        return f"Refused: a request another site made (Sec-Fetch-Site: {fetch_site}) can't call operations."
    external = next((value for value in allowed_origins if host.lower() in _authorities(value)), None)
    if external is not None:
        expected = {f"https://{name}" for name in _authorities(external)}
    else:
        name, given = _split(host)
        default = port in DEFAULT_PORTS and given == ""
        if name not in hosts or (port is not None and given != str(port) and not default):
            where = f" on port {port}" if port is not None else ""
            return (
                f"Refused: this server answers requests to {', '.join(sorted(hosts))}{where}, not {host!r}. {host_help}"
            )
        expected = {f"http://{host.lower()}", f"https://{host.lower()}"}
    if origin is not None and origin.lower() not in expected:
        return f"Refused: a request from another origin ({origin}) can't call operations; use the CLI or MCP."
    return None


def _authorities(origin: str) -> frozenset[str]:
    """The configured HTTPS authority, including the equivalent explicit default port."""
    authority = origin.removeprefix("https://")
    _, port = _split(authority)
    return frozenset({authority}) if port else frozenset({authority, f"{authority}:443"})


def _split(host: str) -> tuple[str, str]:
    """A Host header's name and port; `[::1]:8000` keeps its brackets."""
    host = host.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        if end == -1:
            return host, ""
        rest = host[end + 1 :]
        return host[: end + 1], rest[1:] if rest.startswith(":") else ""
    name, _, port = host.partition(":")
    return name, port
