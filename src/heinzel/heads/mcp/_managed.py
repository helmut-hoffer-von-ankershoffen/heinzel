"""Private TLS identity and instance shutdown for the managed loopback MCP worker."""

import hashlib
import hmac
import re
import ssl
from pathlib import Path

from heinzel.kernel import HeinzelError, boot, private_read

CERTIFICATE = "tls-cert.pem"
PRIVATE_KEY = "tls-key.pem"
STOP_PATH = "/_managed-stop"
STOP_HEADER = "X-Heinzel-Stop"
DIGEST = re.compile(r"[a-f0-9]{64}\Z")


def certificate(directory: Path, expected: str) -> str:
    """Read the exact generation certificate without falling back to endpoint or system trust.

    Raises:
        HeinzelError: If the certificate identity differs from the retained digest.
    """
    pem = private_read(directory / CERTIFICATE)
    if not DIGEST.fullmatch(expected) or not hmac.compare_digest(hashlib.sha256(pem.encode()).hexdigest(), expected):
        msg = "The managed MCP TLS identity could not be verified."
        raise HeinzelError(msg)
    return pem


def client_context(directory: Path, expected: str) -> ssl.SSLContext:
    """Trust one self-signed non-CA leaf, with required verification and the loopback IP SAN."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cadata=certificate(directory, expected))
    return context


def serve(port: int, expected: str, stop_sha256: str) -> None:
    """Serve the standard MCP application plus a private generation-bound shutdown request.

    Raises:
        HeinzelError: If the generation inputs or certificate cannot be verified.
    """
    import uvicorn  # noqa: PLC0415 - only the private worker needs the HTTP server
    from starlette.requests import Request  # noqa: PLC0415
    from starlette.responses import Response  # noqa: PLC0415

    from heinzel.heads.mcp import http_server  # noqa: PLC0415 - ordinary MCP construction

    directory = Path.cwd()
    certificate(directory, expected)
    private_read(directory / PRIVATE_KEY)
    if not DIGEST.fullmatch(stop_sha256) or not 0 < port <= 65535:  # noqa: PLR2004 - TCP port bound
        msg = "The managed MCP worker inputs could not be verified."
        raise HeinzelError(msg)
    mcp, security = http_server(boot(), "127.0.0.1")
    app = mcp.streamable_http_app(host="127.0.0.1", transport_security=security)

    async def shutdown(request: Request) -> Response:
        supplied = request.headers.getlist(STOP_HEADER)
        if (
            request.url.scheme != "https"
            or request.client is None
            or request.client.host != "127.0.0.1"
            or request.headers.getlist("host") != [f"127.0.0.1:{port}"]
            or request.headers.getlist("origin")
            or request.url.query
            or len(supplied) != 1
            or not hmac.compare_digest(hashlib.sha256((supplied[0] + "\n").encode()).hexdigest(), stop_sha256)
        ):
            return Response(status_code=403)
        server.should_exit = True
        return Response(status_code=204)

    app.add_route(STOP_PATH, shutdown, methods=["POST"])
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            ssl_certfile=str(directory / CERTIFICATE),
            ssl_keyfile=str(directory / PRIVATE_KEY),
            proxy_headers=False,
            access_log=False,
            timeout_graceful_shutdown=5,
        )
    )
    server.run()
