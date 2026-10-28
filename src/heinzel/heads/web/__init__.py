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
checks each file again as it reads it (ADR 0044). Themes' logos are served the same way at
`/theme-assets/<theme>/<light|dark|mark>.svg`, as `image/svg+xml` under a CSP of their own that loads and runs nothing.
Soundscapes' audio files are served the same way at `/sound-assets/<name>/<layer-n|earcon-kind>.<ext>`, typed by their
checked signature (ADR 0058). The page itself (`index.html`) admits only fixed frame origins from code: the map origin
only while a weather module is installed, and a stream origin only while the soundscape in effect is a stream. The
configuration is read for each page, and any error admits no stream origin.

`POST /api/restart` restarts the server in place, for changes a running process can't load, such as an added plugin
(`restart_required`, ADR 0047): the process executes `hz` again with the same arguments, handing over its listening
socket and its token, so the open tab keeps its URL and its token and only waits a moment.

Ctrl-C stops `hz ui` at once: the notice stream and the terminals end before the server waits for them
(`_shutdown.py`).
"""

import hashlib
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

from heinzel.heads import api
from heinzel.heads._stream import StreamProgress
from heinzel.heads.web import _terminal
from heinzel.heads.web._devices import Devices
from heinzel.heads.web._lifecycle import CANT_RESTART as CANT_RESTART
from heinzel.heads.web._lifecycle import add_lifecycle
from heinzel.heads.web._pairing import add_pairing
from heinzel.heads.web._ports import wait_for_port
from heinzel.heads.web._shutdown import GRACE_SECONDS, Stopping
from heinzel.heads.web._shutdown import server as stopping_server
from heinzel.heads.web._stream import add_stream
from heinzel.heads.web._updates import add_updates
from heinzel.kernel import (
    FONT_ROUTE,
    KERNEL_SECTION,
    LOGO_CSP,
    LOGO_ROUTE,
    SOUND_CSP,
    SOUND_ROUTE,
    STREAM_PROVIDERS,
    Config,
    HeinzelError,
    Kernel,
    LocalWebRuntime,
    Progress,
    Settings,
    SoundAssets,
    ThemeFonts,
)

HEAD = "web"
STATIC = Path(__file__).parent / "static"
"""Where the build puts the app (`make web-build`, or the wheel's build hook); not in git."""
HOST = "127.0.0.1"
OK = 200
SPLASH_SCRIPT = "'sha256-pN9jYRjUn1PxYq7efc0qmdmmc/sy6sTVaGnaajkcymU='"
"""The one inline script allowed, by its hash: the splash's in `web/index.html`, which paints the last theme before the
app loads (ADR 0046). A test recomputes it from the file, so a changed script fails until this changes with it."""
CSP = (
    f"default-src 'none'; script-src 'self' {SPLASH_SCRIPT}; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; manifest-src 'self'; worker-src 'self'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)
"""Only this origin, for everything; no frames, no forms posting elsewhere, and no inline script but the splash's, by
hash. Inline styles are allowed because the terminal (xterm.js) writes its measurements into `<style>` elements, and
the splash's critical CSS is one."""
CSP_HEADER = "Content-Security-Policy"
SECURITY_HEADERS = {
    CSP_HEADER: CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}
"""How others may treat our answers; none of them limits what the page embeds. Never add COEP `require-corp`: it
would block the stream soundscape's player (ADR 0058)."""
NO_CACHE = frozenset({"sw.js", "manifest.webmanifest", "favicon.svg"})
"""Top-level files a browser must check for a new version every time: the service worker and the manifest."""
mimetypes.add_type("application/manifest+json", ".webmanifest")
NOT_FOUND = 404
FONT_THEME = re.compile(r"[a-z](?:-?[a-z0-9]){0,63}")
FONT_FILE = re.compile(r"([0-9]{1,2})\.woff2")
LOGO_FILE = re.compile(r"(light|dark|mark|heinzel-(?:hat-(?:young|grown|old)|outfit-(?:baby|young|grown|old)))\.svg")
SOUND_FILE = re.compile(r"(layer-[0-9]|earcon-[a-z]{1,16})\.(ogg|opus|mp3)")
IMMUTABLE = "public, max-age=31536000, immutable"
RESUME = "HEINZEL_UI_RESUME"
"""How a restarting `hz ui` hands its listening socket and token to the process it becomes: `<fd>:<token>`. A
variable of this process only, read and removed at start; never a setting, so no file can set it."""
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,}$")
Execute = Callable[[str, list[str], dict[str, str]], object]
"""Replaces the process: `os.execve`, or a fake in tests."""


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
    app = api.build(
        kernel,
        port=port,
        allowed_origins=lambda: kernel.resolve(LocalWebRuntime).loaded_origins,
        head=HEAD,
        token=token,
        docs=False,
        accept=accept,
    )

    add_lifecycle(app, kernel, restart)
    return app


def build(  # noqa: PLR0913 - all but the kernel by keyword
    kernel: Kernel,
    *,
    port: int | None,
    token: str,
    static: Path | None = STATIC,
    devices: Devices | None = None,
    restart: Callable[[], None] | None = None,
    stopping: Stopping | None = None,
) -> FastAPI:
    """The app `hz ui` serves: the built web app at `/` and the web head's API, behind the token, at `/api`.

    Args:
        kernel: The kernel whose operations the API exposes.
        port: The port it listens on; requests naming another are refused.
        token: The session token every API request must carry, unless it carries a paired device's.
        static: The built app; None serves the API only (`make web-dev`, where Vite serves the app).
        devices: The paired devices; the user's by default.
        restart: Restarts the server for `POST /api/restart`; None where it can't.
        stopping: Ends the notice stream and the terminals when the server stops; None where nothing stops it.

    Raises:
        HeinzelError: If `static` holds no built app.
    """
    if static is not None and not (static / "index.html").is_file():
        raise HeinzelError(NOT_BUILT)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    terminal_sessions = _terminal.OriginSessions()
    runtime = LocalWebRuntime(
        port=port,
        loaded_origins=kernel.settings.ui_allowed_origins,
        _on_origins_removed=terminal_sessions.revoke_origins,
    )
    kernel.bind(LocalWebRuntime, runtime)
    kernel.bind(Progress, StreamProgress())  # operations' events reach the stream of the request that runs them

    @app.middleware("http")
    async def guarded(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        headers = request.headers
        if problem := api.request_refusal(
            headers,
            api.LOOPBACK,
            port,
            allowed_origins=runtime.loaded_origins,
            host_help=api.WEB_HOST_HELP,
        ):
            response: Response = JSONResponse(status_code=api.FORBIDDEN, content={"error": problem})
        else:
            response = await call_next(request)
        secure(response, request.url.path)
        return response

    paired = devices or devices_for_user()
    stopping = stopping or Stopping()
    _terminal.add_route(
        app,
        token=token,
        port=port,
        allowed_origins=lambda: runtime.loaded_origins,
        accept=paired.check,
        stopping=stopping,
        sessions=terminal_sessions,
        register_origin=runtime.register_if_origin_allowed,
    )  # before the mount, which takes /api

    endpoints = api_app(
        kernel,
        port=port,
        token=token,
        restart=restart,
        accept=lambda presented: device.id if (device := paired.check(presented)) else None,
    )
    add_pairing(endpoints, paired)
    add_updates(endpoints, kernel, static, restartable=restart is not None)
    add_stream(endpoints, kernel, stopping)  # behind the same middleware: Host, Origin and the token
    app.mount("/api", endpoints)
    add_fonts(app, kernel)
    add_logos(app, kernel)
    add_sounds(app, kernel)
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
    def page(path: str) -> Response:
        # Not async: the page's CSP reads the configuration, so FastAPI runs it in its thread pool.
        # Top-level files as themselves; every other path is a route of the app.
        if path in files:
            headers = {"Cache-Control": "no-cache"} if path in NO_CACHE else None
            return FileResponse(files[path], headers=headers)
        return FileResponse(index, headers={"Cache-Control": "no-store", CSP_HEADER: page_csp(kernel)})

    return app


OSM_FRAME_ORIGIN = "https://www.openstreetmap.org"
"""The fixed map document origin, admitted only while a weather module is installed."""


def page_csp(kernel: Kernel) -> str:
    """The page CSP admits only fixed origins needed by currently installed capabilities.

    A map is available only with a weather plugin, and a streaming frame only while a known stream is in effect. No
    origin comes from plugin, user, or configuration input.
    """
    origins: list[str] = []
    if any(record.qualname == "weather" or record.qualname.startswith("weather.") for record in kernel.modules):
        origins.append(OSM_FRAME_ORIGIN)
    try:
        provider = kernel.resolve(SoundAssets).stream_provider() if kernel.provides(SoundAssets) else None
    except Exception:  # noqa: BLE001 - fail closed: the page loads with the strict CSP, sound just can't stream
        provider = None
    stream = STREAM_PROVIDERS.get(provider) if provider else None
    if stream:
        origins.append(stream.frame_origin)
        origins.extend(stream.additional_frame_origins)
    return f"{CSP}; frame-src {' '.join(dict.fromkeys(origins))}" if origins else CSP


def secure(response: Response, path: str) -> None:
    """Add the security headers to the answer for `path`, and what the answers of some paths need besides.

    The page's own CSP (`page_csp`) is kept: only it may admit a frame, and only by extending `CSP`.
    """
    own = response.headers.get(CSP_HEADER)
    response.headers.update(SECURITY_HEADERS)
    if (
        own
        and own.startswith(CSP + "; frame-src ")
        and response.headers.get("content-type", "").startswith("text/html")
    ):
        response.headers[CSP_HEADER] = own
    if response.status_code != OK:
        return
    if path.startswith(SOUND_ROUTE + "/"):
        response.headers[CSP_HEADER] = SOUND_CSP  # an audio file opened on its own loads and runs nothing
    if path.startswith(LOGO_ROUTE + "/"):
        response.headers["Content-Security-Policy"] = LOGO_CSP  # an SVG opened on its own loads and runs nothing
    if path.startswith("/assets/"):
        response.headers["Cache-Control"] = IMMUTABLE  # file names carry their hash


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


def add_logos(app: FastAPI, kernel: Kernel) -> None:
    """Serve the themes' logos at `LOGO_ROUTE`, outside `/api`, behind the app's middleware (ADR 0044)."""

    @app.get(LOGO_ROUTE + "/{theme}/{file}", include_in_schema=False)
    def theme_logo(theme: str, file: str, v: str | None = None) -> Response:
        # Not async: it reads files, so FastAPI runs it in its thread pool and other requests don't wait.
        return logo_response(kernel, theme, file, version=v)


def logo_response(kernel: Kernel, theme: str, file: str, *, version: str | None = None) -> Response:
    """An available theme's logo or content-versioned avatar layer, or 404; tokenless original artwork."""
    # Checked against fixed patterns, then looked up by theme name and logo name: a request never builds a path.
    name = LOGO_FILE.fullmatch(file)
    content = None
    if FONT_THEME.fullmatch(theme) and name and kernel.provides(ThemeFonts):
        try:
            content = kernel.resolve(ThemeFonts).logo(theme, name.group(1))
        except HeinzelError:  # system.theme can't be built (a module it needs is off): no theme ships a logo
            content = None
    if (
        content is not None
        and name
        and name.group(1).startswith("heinzel-")
        and version != hashlib.sha256(content).hexdigest()[:16]
    ):
        content = None
    if content is None:
        return JSONResponse(status_code=NOT_FOUND, content={"error": "No theme ships that logo."})
    # The list's URL carries the content's version (`?v=`), so a changed logo is a new URL: the answer is immutable.
    return Response(content, media_type="image/svg+xml", headers={"Cache-Control": IMMUTABLE})


def add_sounds(app: FastAPI, kernel: Kernel) -> None:
    """Serve the soundscapes' audio files at `SOUND_ROUTE`, outside `/api`, behind the app's middleware (ADR 0058)."""

    @app.get(SOUND_ROUTE + "/{soundscape}/{file}", include_in_schema=False)
    def soundscape_file(soundscape: str, file: str) -> Response:
        # Not async: it reads files, so FastAPI runs it in its thread pool and other requests don't wait.
        return sound_response(kernel, soundscape, file)


def sound_response(kernel: Kernel, soundscape: str, file: str) -> Response:
    """Audio file `<layer-n|earcon-kind>.<ext>` of the available `soundscape`, or 404; served without the token.

    Its type is the one its checked signature says, and the extension asked for must be the declared file's.
    """
    # Checked against fixed patterns, then looked up by name and part: a request never builds a path.
    match = SOUND_FILE.fullmatch(file)
    found = None
    if FONT_THEME.fullmatch(soundscape) and match and kernel.provides(SoundAssets):
        try:
            found = kernel.resolve(SoundAssets).asset(soundscape, match.group(1), match.group(2))
        except Exception:  # noqa: BLE001 - system.sound can't be built or read: no soundscape ships files
            found = None
    if found is None:
        return JSONResponse(status_code=NOT_FOUND, content={"error": "No soundscape ships that file."})
    content, media_type = found
    # The list's URL carries the content's version (`?v=`), so a changed file is a new URL: the answer is immutable.
    return Response(content, media_type=media_type, headers={"Cache-Control": IMMUTABLE})


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
    waiting: Callable[[str], None] = print,
) -> None:
    """Serve the web head on localhost until Ctrl-C stops it, with a new token, and open the browser at it.

    It returns once stopped; a restart (`POST /api/restart`) replaces the process instead.

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
        waiting: Reports a bounded wait when a recently stopped UI still holds the requested port.

    Raises:
        HeinzelError: If the app isn't built.
    """
    import uvicorn  # noqa: PLC0415 - only needed when serving

    if static is not None and not (static / "index.html").is_file():
        raise HeinzelError(NOT_BUILT)  # before binding a port, so nothing is left open
    # Read and removed at once, so nothing this process starts inherits the token.
    sock, token, resumed = _session(os.environ.pop(RESUME, None), port, waiting)
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

    stopping = Stopping()
    try:
        app = build(
            kernel, port=bound, token=token, static=static, restart=restart if restartable else None, stopping=stopping
        )
    except BaseException:
        sock.close()
        raise
    config = uvicorn.Config(app, log_level="warning", lifespan="off", timeout_graceful_shutdown=GRACE_SECONDS)
    server = stopping_server(config, stopping)
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
    if handover and not stopping.interrupted:  # Ctrl-C while restarting stops it
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


def _session(
    handed_over: str | None, port: int, waiting: Callable[[str], None] = print
) -> tuple[socket.socket, str, bool]:
    """The listening socket and token: those a restarting `hz ui` handed over, else new ones; and whether resumed.

    Raises:
        HeinzelError: If a fixed port stays busy after the bounded wait.
        OSError: If binding fails for another reason.
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
    return wait_for_port(lambda: free_socket(HOST, port), port, waiting), secrets.token_urlsafe(32), False
