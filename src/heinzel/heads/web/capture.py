"""Developer-only ASGI entry point for a source-reviewed, network-fenced gallery fixture."""

import os
import platform
import secrets
import socket
import stat
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from heinzel.heads.web import HOST, RESUME, STATIC, build, session_url
from heinzel.heads.web._devices import Devices
from heinzel.heads.web._shutdown import GRACE_SECONDS, KEEP_ALIVE_SECONDS, Stopping, server
from heinzel.kernel import FILENAME, KERNEL_SECTION, Config, HeinzelError, Settings, boot

_PROFILE_PATHS = ("CONFIG_HOME", "DATA_DIR", "CACHE_DIR", "STATE_DIR", "LOG_DIR")
_FIRST_INHERITED_FD = 3
_PRIVATE_MODE = 0o700
_MIN_PORT = 1024
_MAX_PORT = 65535
# Darwin SDK netinet/tcp.h and tcp_fsm.h; tcp_connection_info starts with tcpi_state.
_DARWIN_TCP_CONNECTION_INFO = 0x106
_DARWIN_TCP_LISTEN = b"\x01"


def _capture_config(profile: Path) -> Config:
    """Read only the private fixture configuration, excluding machine and project configuration.

    Raises:
        HeinzelError: The environment does not identify an isolated capture profile.
    """
    message = "Capture requires a private fixture profile and isolated configuration"
    if sys.platform not in {"darwin", "linux"} or RESUME in os.environ or "HZ_CONFIG" in os.environ:
        raise HeinzelError(message)
    try:
        root = profile.resolve(strict=True)
        info = root.stat()
        if (
            root != profile
            or root != Path.home()
            or root != Path.cwd()
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != _PRIVATE_MODE
            or os.environ.get("HZ_SYSTEM_CREDENTIAL__BACKEND") != "environment"
        ):
            raise HeinzelError(message)
        for name in _PROFILE_PATHS:
            path = root / name.lower()
            if os.environ.get(f"HZ_{name}") != str(path) or path.resolve() != path:
                raise HeinzelError(message)
        user_file = root / "config_home" / FILENAME
        if user_file.resolve() != user_file or (user_file.exists() and not user_file.is_file()):
            raise HeinzelError(message)
    except (OSError, RuntimeError) as error:
        raise HeinzelError(message) from error
    return Config.for_isolated_home(root)


def _accepting(sock: socket.socket) -> bool:
    if platform.system() == "Darwin":
        return sock.getsockopt(socket.IPPROTO_TCP, _DARWIN_TCP_CONNECTION_INFO, 1) == _DARWIN_TCP_LISTEN
    return sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1


def _adopt_listener(descriptor: int, port: int) -> socket.socket:
    """Take the inherited socket only when it matches the launcher's still-reserved endpoint.

    Raises:
        HeinzelError: The descriptor is invalid or is outside the capture contract.
    """
    message = "Capture requires the inherited exclusive IPv4 loopback listener on the selected port"
    if (
        type(descriptor) is not int
        or descriptor < _FIRST_INHERITED_FD
        or type(port) is not int
        or not _MIN_PORT <= port <= _MAX_PORT
    ):
        raise HeinzelError(message)
    try:
        sock = socket.socket(fileno=descriptor)
    except (OSError, OverflowError) as error:
        raise HeinzelError(message) from error
    try:
        accepted = (
            sock.family == socket.AF_INET
            and sock.type == socket.SOCK_STREAM
            and sock.getsockname() == (HOST, port)
            and _accepting(sock)
            and not sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR)
            and not sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT)
        )
        if accepted:
            sock.set_inheritable(False)
    except OSError as error:
        sock.detach()
        raise HeinzelError(message) from error
    if not accepted:
        sock.detach()  # the rejected descriptor was never ours
        raise HeinzelError(message)
    return sock


def serve_capture_fixture(
    descriptor: int,
    *,
    port: int,
    profile: Path,
    static: Path | None = STATIC,
    announce: Callable[[str], None] = print,
) -> None:
    """Serve real ASGI on the exact listener retained by the fenced capture launcher.

    Call only from the reviewed bootstrap after it creates the fixture profile and enters its OS network
    fence. The launcher must retain its exclusive listener until teardown and pass the exact port separately
    from the inherited FD. Successful adoption transfers the child's copy to this function.

    Raises:
        HeinzelError: The profile or listener is invalid, or update checks or external UI origins are enabled.
    """
    import uvicorn  # noqa: PLC0415 - only needed when serving

    config = _capture_config(profile)
    settings = config.settings(Settings, KERNEL_SECTION)
    if settings.update_check or settings.ui_allowed_origins:
        message = "Capture requires update checks and external UI origins to be disabled"
        raise HeinzelError(message)
    with _adopt_listener(descriptor, port) as sock:
        kernel = boot(settings, config=config)
        token = secrets.token_urlsafe(32)
        stopping = Stopping()
        app = build(
            kernel,
            port=port,
            token=token,
            static=static,
            devices=Devices(settings.state_dir / "web"),
            stopping=stopping,
        )
        runner = server(
            uvicorn.Config(
                app,
                log_level="warning",
                lifespan="off",
                timeout_graceful_shutdown=GRACE_SECONDS,
                timeout_keep_alive=KEEP_ALIVE_SECONDS,
            ),
            stopping,
        )
        finished = threading.Event()

        def announce_when_ready() -> None:
            while not finished.wait(0.02):
                if runner.started:
                    announce(session_url(port, token))
                    return

        ready = threading.Thread(target=announce_when_ready, daemon=True)
        ready.start()
        try:
            runner.run(sockets=[sock])
        finally:
            finished.set()
            ready.join()
