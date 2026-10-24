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

Themes' font files are served outside `/api`, at `/theme-fonts/<theme>/<index>.woff2`, without the token: the browser
fetches a font for `FontFace` or `@font-face` itself and can't add a header. They pass the same Host, Origin and
`Sec-Fetch-Site` checks, hold nothing private (what an installed plugin ships), and come through `ThemeFonts`, which
checks each file again as it reads it (ADR 0044).

`POST /api/restart` restarts the server in place, for changes a running process can't load, such as an added plugin
(`restart_required`, ADR 0047): the process executes `hz` again with the same arguments, handing over its listening
socket and its token, so the open tab keeps its URL and its token and only waits a moment.
"""

import mimetypes
import os
import re
import secrets
import socket
import sys
import threading
import webbrowser
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from heinzel.heads import api
from heinzel.heads.web._devices import Devices
from heinzel.heads.web._pairing import add_pairing
from heinzel.heads.web._stream import add_stream
from heinzel.kernel import FONT_ROUTE, KERNEL_SECTION, Config, HeinzelError, Kernel, Settings, ThemeFonts

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
NOT_FOUND = 404
FONT_THEME = re.compile(r"[a-z](?:-?[a-z0-9]){0,63}")
FONT_FILE = re.compile(r"([0-9]{1,2})\.woff2")
IMMUTABLE = "public, max-age=31536000, immutable"
RESUME = "HEINZEL_UI_RESUME"
"""How a restarting `hz ui` hands its listening socket and token to the process it becomes: `<fd>:<token>`. A
variable of this process only, read and removed at start; never a setting, so no file can set it."""
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,}$")
ACCEPTED = 202
CANT_RESTART = "This server can't restart itself (it isn't `hz ui`, e.g. `make web-dev`); stop it and start it again."
Execute = Callable[[str, list[str], dict[str, str]], object]
"""Replaces the process: `os.execve`, or a fake in tests."""


class Restart(BaseModel):
    """The answer to `POST /api/restart`: the server stops and starts again on the same port with the same token."""

    restarting: bool = Field(description="Always true: poll any endpoint until it answers again.")
    message: str = Field(description="What happens, for the person waiting.")


NOT_BUILT = (
    "The web app isn't built into this installation. In a checkout of the repository run `make web-build`; an "
    "installed release ships it."
)


def devices_for_user() -> Devices:
    """The paired devices in the user's state directory: a project's `hz.toml` can't move them."""
    settings = Config.load(Path.home()).settings(Settings, KERNEL_SECTION)
    return Devices(settings.state_dir / "web")


def api_app(
    kernel: Kernel,
    *,
    port: int | None,
    token: str,
    restart: Callable[[], None] | None = None,
    accept: Callable[[str], str | None] | None = None,
) -> FastAPI:
    """The web head's API: the API head's endpoints for head `web`, behind the token, and `POST /restart`.

    Args:
        kernel: The kernel whose operations the API exposes.
        port: The port it listens on; requests naming another are refused.
        token: The session token every API request must carry.
        restart: Restarts the server; None where it can't, and `/restart` answers 400.
        accept: Which other bearer token is accepted too (a paired device's), as `api.build` takes it.
    """
    app = api.build(kernel, port=port, head=HEAD, token=token, docs=False, accept=accept)

    def restart_server(response: Response) -> Restart:
        if restart is None:
            raise HeinzelError(CANT_RESTART)
        restart()
        response.status_code = ACCEPTED
        return Restart(
            restarting=True, message="Restarting hz ui to load what changed; it answers again in a few seconds."
        )

    app.add_api_route(
        "/restart",
        restart_server,
        methods=["POST"],
        summary="Restart hz ui to load changes a running server can't, such as an added plugin.",
        description="The process runs `hz` again with the same port and token, so the page keeps working: poll an "
        "endpoint until it answers. A server that isn't `hz ui` (`make web-dev`) answers 400.",
        response_model=Restart,
        status_code=ACCEPTED,
        tags=["web"],
        operation_id="web_restart",
    )
    return app


def build(
    kernel: Kernel,
    *,
    port: int | None,
    token: str,
    static: Path | None = STATIC,
    devices: Devices | None = None,
    restart: Callable[[], None] | None = None,
) -> FastAPI:
    """The app `hz ui` serves: the built web app at `/` and the web head's API, behind the token, at `/api`.

    Args:
        kernel: The kernel whose operations the API exposes.
        port: The port it listens on; requests naming another are refused.
        token: The session token every API request must carry, unless it carries a paired device's.
        static: The built app; None serves the API only (`make web-dev`, where Vite serves the app).
        devices: The paired devices; the user's by default.
        restart: Restarts the server for `POST /api/restart`; None where it can't.

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
            response.headers["Cache-Control"] = IMMUTABLE  # file names carry their hash
        return response

    paired = devices or devices_for_user()
    endpoints = api_app(
        kernel,
        port=port,
        token=token,
        restart=restart,
        accept=lambda presented: device.id if (device := paired.check(presented)) else None,
    )
    add_pairing(endpoints, paired)
    add_stream(endpoints, kernel)  # behind the same middleware: Host, Origin and the token
    app.mount("/api", endpoints)
    add_fonts(app, kernel)
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


def add_fonts(app: FastAPI, kernel: Kernel) -> None:
    """Serve the themes' font files at `FONT_ROUTE`, outside `/api`, behind the app's middleware (ADR 0044)."""

    @app.get(FONT_ROUTE + "/{theme}/{file}", include_in_schema=False)
    def theme_font(theme: str, file: str) -> Response:
        # Not async: it reads files, so FastAPI runs it in its thread pool and other requests don't wait.
        return font_response(kernel, theme, file)


def font_response(kernel: Kernel, theme: str, file: str) -> Response:
    """Font `<index>.woff2` of the available theme `theme`, or 404; served without the token (ADR 0044)."""
    # Checked against fixed patterns, then looked up by theme name and index: a request never builds a path.
    index = FONT_FILE.fullmatch(file)
    content = None
    if FONT_THEME.fullmatch(theme) and index and kernel.provides(ThemeFonts):
        try:
            content = kernel.resolve(ThemeFonts).font(theme, int(index.group(1)))
        except HeinzelError:  # system.theme can't be built (a module it needs is off): no theme ships fonts
            content = None
    if content is None:
        return JSONResponse(status_code=NOT_FOUND, content={"error": "No theme ships that font."})
    # The list's URL carries the content's version (`?v=`), so a changed font is a new URL: the answer is immutable.
    return Response(content, media_type="font/woff2", headers={"Cache-Control": IMMUTABLE})


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
    restartable: bool = True,
    execute: Execute = os.execve,
) -> None:
    """Serve the web head on localhost until interrupted, with a new token, and open the browser at it.

    Started by a restarting `hz ui` (the `HEINZEL_UI_RESUME` variable), it takes over that process's socket and
    token instead and opens no browser: the tab is still open.

    Args:
        kernel: The booted kernel.
        port: The port to listen on; 0 picks a free one.
        browser: Open the default browser at the session URL once the server is up.
        announce: Called with the session URL once the server is up.
        static: The built app; None serves the API only, for `make web-dev`.
        base: The origin of the URL to announce, when another server serves the app (Vite in `make web-dev`).
        restartable: Whether `POST /api/restart` runs this process's command again; false where the command isn't
            `hz ui` (`make web-dev`).
        execute: Replaces the process on restart; a fake in tests.

    Raises:
        HeinzelError: If the app isn't built.
    """
    import uvicorn  # noqa: PLC0415 - only needed when serving

    if static is not None and not (static / "index.html").is_file():
        raise HeinzelError(NOT_BUILT)  # before binding a port, so nothing is left open
    # Read and removed at once, so nothing this process starts inherits the token.
    sock, token, resumed = _session(os.environ.pop(RESUME, None), port)
    browser = browser and not resumed
    bound = int(sock.getsockname()[1])
    handover: list[int] = []

    def restart() -> None:
        # A duplicate of the listening socket survives uvicorn closing its own and the exec: connections wait in its
        # backlog until the new process accepts them, so the tab sees a slow answer, never a refused connection.
        if not handover:
            duplicate = os.dup(sock.fileno())
            os.set_inheritable(duplicate, True)  # noqa: FBT003 - os API
            handover.append(duplicate)
        server.should_exit = True

    try:
        app = build(kernel, port=bound, token=token, static=static, restart=restart if restartable else None)
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
    if handover:
        command = [sys.executable, "-m", "heinzel", *sys.argv[1:]]
        execute(sys.executable, command, {**os.environ, RESUME: f"{handover[0]}:{token}"})


def _listening_on_loopback(sock: socket.socket) -> bool:
    """Whether a handed-over socket is what `hz ui` listens on: TCP on 127.0.0.1, accepting connections."""
    try:
        if sock.family != socket.AF_INET or sock.type != socket.SOCK_STREAM or sock.getsockname()[0] != HOST:
            return False
    except OSError:
        return False
    try:
        return bool(sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN))
    except OSError:  # macOS has no SO_ACCEPTCONN; a listening socket is at least not connected to a peer
        try:
            sock.getpeername()
        except OSError:
            return True
        return False


def _session(handed_over: str | None, port: int) -> tuple[socket.socket, str, bool]:
    """The listening socket and token: those a restarting `hz ui` handed over, else new ones; and whether resumed.

    Raises:
        OSError: If `port` is taken.
    """
    fd, _, token = (handed_over or "").partition(":")
    if fd.isdigit() and TOKEN_PATTERN.match(token):
        try:
            sock = socket.socket(fileno=int(fd))
        except OSError:
            pass  # not a socket after all: start a fresh session
        else:
            if _listening_on_loopback(sock):
                os.set_inheritable(sock.fileno(), False)  # noqa: FBT003 - os API; a restart hands it on explicitly
                return sock, token, True
            sock.detach()  # not ours to close: whatever handed it over keeps it
    return free_socket(HOST, port), secrets.token_urlsafe(32), False
