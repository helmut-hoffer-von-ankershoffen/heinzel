"""The web head: a React app (web/ in the repository) that the API head serves, started with `hz ui`.

One origin serves both: the built app at `/`, every path the app routes falling back to its `index.html`, and the API
head's endpoints under `/api`, built for the `web` head. The API part keeps every check of the REST head (a loopback
Host on our port, no other Origin, no cross-site `Sec-Fetch-Site`) and adds a token: `hz ui` makes one per session and
opens the browser with it in the URL's fragment, which never reaches a server or a Referer; the app keeps it in
`sessionStorage` and sends it as `Authorization: Bearer <token>` with every call. Requests without it are refused, so
another page in the browser can't call operations even when it can reach the port. Every response carries a strict
Content Security Policy that allows this origin only (ADR 0039).

`hz ui` listens on one fixed port (`[hz] ui_port`), so an installed app keeps its origin, and with its session token
the app pairs once for a device token it keeps, which every later `hz ui` accepts too (`_devices.py`, ADR 0046). The
app's manifest and service worker are top-level files of the build; the worker shows notifications and caches the
app's shell, never API data.
"""

import mimetypes
import secrets
import socket
import threading
import webbrowser
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from heinzel.heads import api
from heinzel.heads.web._devices import Devices
from heinzel.heads.web._pairing import add_pairing
from heinzel.heads.web._stream import add_stream
from heinzel.kernel import KERNEL_SECTION, Config, HeinzelError, Kernel, Settings

HEAD = "web"
STATIC = Path(__file__).parent / "static"
"""Where the build puts the app (`make web-build`, or the wheel's build hook); not in git."""
HOST = "127.0.0.1"
OK = 200
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'; manifest-src 'self'; worker-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)
"""Only this origin, for everything; no inline scripts or styles, no frames, no forms posting elsewhere."""
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}
NO_CACHE = frozenset({"sw.js", "manifest.webmanifest"})
"""Top-level files a browser must check for a new version every time: the service worker and the manifest."""
mimetypes.add_type("application/manifest+json", ".webmanifest")
NOT_BUILT = (
    "The web app isn't built into this installation. In a checkout of the repository run `make web-build`; an "
    "installed release ships it."
)


def devices_for_user() -> Devices:
    """The paired devices in the user's state directory: a project's `hz.toml` can't move them."""
    settings = Config.load(Path.home()).settings(Settings, KERNEL_SECTION)
    return Devices(settings.state_dir / "web")


def build(
    kernel: Kernel,
    *,
    port: int | None,
    token: str,
    static: Path | None = STATIC,
    devices: Devices | None = None,
) -> FastAPI:
    """The app `hz ui` serves: the built web app at `/` and the web head's API, behind the token, at `/api`.

    Args:
        kernel: The kernel whose operations the API exposes.
        port: The port it listens on; requests naming another are refused.
        token: The session token every API request must carry, unless it carries a paired device's.
        static: The built app; None serves the API only (`make web-dev`, where Vite serves the app).
        devices: The paired devices; the user's by default.

    Raises:
        HeinzelError: If `static` holds no built app.
    """
    if static is not None and not (static / "index.html").is_file():
        raise HeinzelError(NOT_BUILT)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guarded(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        headers = request.headers
        if problem := api.refusal(
            headers.get("host", ""), headers.get("origin"), api.LOOPBACK, port, headers.get("sec-fetch-site")
        ):
            response: Response = JSONResponse(status_code=api.FORBIDDEN, content={"error": problem})
        else:
            response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path.startswith("/assets/") and response.status_code == OK:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"  # file names carry their hash
        return response

    paired = devices or devices_for_user()
    endpoints = api.build(
        kernel,
        port=port,
        head=HEAD,
        token=token,
        docs=False,
        accept=lambda presented: device.id if (device := paired.check(presented)) else None,
    )
    add_pairing(endpoints, paired)
    add_stream(endpoints, kernel)  # behind the same middleware: Host, Origin and the token
    app.mount("/api", endpoints)
    if static is None:
        return app
    if (static / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")
    if (static / "icons").is_dir():  # the installed app's icons (manifest.webmanifest)
        app.mount("/icons", StaticFiles(directory=static / "icons"), name="icons")
    index = static / "index.html"
    # The top-level files (favicon.svg), listed once: a request picks one by name and never builds a path.
    files = {file.name: file for file in static.iterdir() if file.is_file() and file != index}

    @app.get("/{path:path}", include_in_schema=False)
    async def page(path: str) -> Response:
        # Top-level files as themselves; every other path is a route of the app.
        if path in files:
            headers = {"Cache-Control": "no-cache"} if path in NO_CACHE else None
            return FileResponse(files[path], headers=headers)
        return FileResponse(index, headers={"Cache-Control": "no-store"})

    return app


def free_socket(host: str = HOST, port: int = 0) -> socket.socket:
    """A listening socket on `host`; port 0 picks a free one. Bound before the server starts, so no one takes it.

    Raises:
        OSError: If the port is taken.
    """
    # No SO_REUSEADDR: on macOS it would let us bind a port another server already listens on, silently.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
        sock.listen(128)
    except OSError:
        sock.close()
        raise
    return sock


def session_url(port: int, token: str, base: str | None = None) -> str:
    """The URL that opens the app with its token in the fragment, which browsers never send to a server."""
    return f"{base or f'http://{HOST}:{port}'}/#token={token}"


def serve(  # noqa: PLR0913 - all but the kernel by keyword
    kernel: Kernel,
    *,
    port: int = 0,
    browser: bool = True,
    announce: Callable[[str], None] = print,
    static: Path | None = STATIC,
    base: str | None = None,
) -> None:
    """Serve the web head on localhost until interrupted, with a new token, and open the browser at it.

    Args:
        kernel: The booted kernel.
        port: The port to listen on; 0 picks a free one.
        browser: Open the default browser at the session URL once the server is up.
        announce: Called with the session URL once the server is up.
        static: The built app; None serves the API only, for `make web-dev`.
        base: The origin of the URL to announce, when another server serves the app (Vite in `make web-dev`).

    Raises:
        HeinzelError: If the app isn't built.
    """
    import uvicorn  # noqa: PLC0415 - only needed when serving

    token = secrets.token_urlsafe(32)
    if static is not None and not (static / "index.html").is_file():
        raise HeinzelError(NOT_BUILT)  # before binding a port, so nothing is left open
    sock = free_socket(HOST, port)
    bound = int(sock.getsockname()[1])
    try:
        app = build(kernel, port=bound, token=token, static=static)
    except BaseException:
        sock.close()
        raise
    # Open notice streams end on their own within minutes; on Ctrl+C don't wait for them.
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off", timeout_graceful_shutdown=1))
    url = session_url(bound, token, base)

    def when_started() -> None:
        while not server.started and not server.should_exit:
            threading.Event().wait(0.05)
        if server.started:
            announce(url)
            if browser:
                webbrowser.open(url)

    threading.Thread(target=when_started, daemon=True).start()
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()
