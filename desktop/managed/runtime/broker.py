"""Own a loopback listener without importing installed plugin code; stop owned workers when the controller closes."""

# ruff: noqa: INP001 - standalone sealed helper, intentionally outside the installed application imports

from __future__ import annotations

import fcntl
import hashlib
import http.client
import json
import os
import secrets
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import cast

MAX_INPUT = 16 * 1024
MAX_PORT = 65535
INSTANCE_LENGTH = 32
CRASH_WINDOW = 60
MAX_STARTS = 3


class LaunchError(ValueError):
    """A closed launch record or private profile could not be verified."""

    def __init__(self) -> None:
        """Keep protocol errors free of file paths and credentials."""
        super().__init__("The owned desktop backend could not start. Repair its installation.")


@dataclass(frozen=True, slots=True)
class Launch:
    """Checked private parent-pipe input; parsing requires only the standard library before plugin startup."""

    nonce: str
    port: int
    worker: str
    worker_sha256: str
    version: str
    ui_sha256: str
    profile: str
    deployment: str
    generation: int


def announce(event: str, **values: object) -> None:
    """Write only the private controller protocol, never arbitrary worker output."""
    sys.stdout.write(json.dumps({"event": event, **values}) + "\n")
    sys.stdout.flush()


def configuration() -> Launch:
    """Read one bounded launch from the parent pipe, before starting plugin code.

    Raises:
        LaunchError: If the launch is not a closed bounded protocol record.
        ValueError: If the input is not JSON.
    """
    line = sys.stdin.buffer.readline(MAX_INPUT + 1)
    value = json.loads(line)
    if len(line) > MAX_INPUT or not isinstance(value, dict):
        raise LaunchError
    if set(value) != {
        "nonce",
        "port",
        "worker",
        "worker_sha256",
        "version",
        "ui_sha256",
        "profile",
        "deployment",
        "generation",
    }:
        raise LaunchError
    if type(value["port"]) is not int or not 0 <= value["port"] <= MAX_PORT:
        raise LaunchError
    if type(value["generation"]) is not int or value["generation"] < 1:
        raise LaunchError
    if not all(isinstance(value[key], str) for key in value if key not in {"port", "generation"}):
        raise LaunchError
    return Launch(
        **{key: cast("str", value[key]) for key in value if key not in {"port", "generation"}},
        port=value["port"],
        generation=value["generation"],
    )


def stop_worker(worker: subprocess.Popen[bytes]) -> bool:
    """Close this worker's parent lease, then fence only the group this broker created if graceful exit fails."""
    if worker.stdin is not None:
        worker.stdin.close()
    try:
        worker.wait(timeout=8)
    except subprocess.TimeoutExpired:
        for requested in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(worker.pid, requested)
            except ProcessLookupError:
                break
            try:
                worker.wait(timeout=2)
                break
            except subprocess.TimeoutExpired:
                continue
        return False  # Forced stop is uncertain, not evidence that effects were rolled back.
    else:
        return True


def response_bytes(
    connection: http.client.HTTPConnection, path: str, headers: dict[str, str], limit: int
) -> bytes | None:
    """Read a bounded successful response from the already selected owner, refusing redirects."""
    connection.request("GET", path, headers=headers)
    with connection.getresponse() as response:
        return response.read(limit + 1) if response.status == HTTPStatus.OK else None


def probe(port: int, token: str, version: str, ui_digest: str) -> dict[str, object] | None:
    """Require authenticated loaded identity and exact UI bytes; never follow redirects or use ambient proxies."""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=0.3)) as connection:
            body = response_bytes(connection, "/api/lifecycle", headers, MAX_INPUT)
            if body is None or len(body) > MAX_INPUT:
                return None
            value = json.loads(body)
            if (
                not isinstance(value, dict)
                or value.get("version") != version
                or not isinstance(value.get("instance"), str)
                or len(value["instance"]) != INSTANCE_LENGTH
            ):
                return None
            html = response_bytes(connection, "/", headers, 2 * 1024 * 1024)
            if html is None or len(html) > 2 * 1024 * 1024 or hashlib.sha256(html).hexdigest() != ui_digest:
                return None
    except (OSError, ValueError, http.client.HTTPException):
        return None
    else:
        return value


def _directory(path: Path) -> None:
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise LaunchError


def _lock_file(lock_path: Path) -> int:
    try:
        lock = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        lock = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    details = os.fstat(lock)
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.getuid()
        or details.st_mode & 0o077
        or details.st_nlink != 1
    ):
        os.close(lock)
        raise LaunchError
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(lock)
        raise
    return lock


def _private_bytes(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o077
            or metadata.st_size > MAX_INPUT
        ):
            raise LaunchError
        with os.fdopen(os.dup(descriptor), "rb") as source:
            encoded = source.read(MAX_INPUT + 1)
        if len(encoded) > MAX_INPUT:
            raise LaunchError
        return encoded
    finally:
        os.close(descriptor)


def ownership(profile: Path) -> int:
    """Hold an exclusive owner-only profile lock, independently of the selected HTTP port.

    Raises:
        LaunchError: If profile ownership is not private and regular.
        OSError: If another controller owns this profile.
    """
    _directory(profile)
    return _lock_file(profile / "backend.lock")


def _digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == hashlib.sha256().digest_size * 2
        and all(character in "0123456789abcdef" for character in value)
    )


def _starting_transition(lifecycle: Path, options: Launch) -> None:
    try:
        pending = json.loads(_private_bytes(lifecycle / "transition.json"))
    except FileNotFoundError:
        return
    if (
        not isinstance(pending, dict)
        or pending.get("phase") not in {"switched", "verified"}
        or pending.get("candidate") != options.deployment
        or type(pending.get("generation")) is not int
        or pending["generation"] != options.generation
    ):
        raise LaunchError


def activated_ownership(options: Launch) -> int:
    """Linearize the selected deployment with backend ownership before executing any installed worker.

    The controller may have read an older activation. Taking the lifecycle lock first matches the transition
    authority's lock order; retaining backend ownership then prevents a switch until this broker stops.

    Raises:
        LaunchError: If the selected generation, deployment or worker is stale or unsafe.
        OSError: If a lifecycle transition or backend already owns this profile.
        ValueError: If private metadata is invalid JSON.
    """
    profile = Path(options.profile)
    _directory(profile)
    lifecycle = profile / "lifecycle"
    _directory(lifecycle)
    lock = _lock_file(lifecycle / "lifecycle.lock")
    try:
        active = json.loads(_private_bytes(lifecycle / "active.json"))
        if (
            not isinstance(active, dict)
            or set(active) != {"schema", "generation", "deployment", "previous"}
            or type(active["schema"]) is not int
            or active["schema"] != 1
            or type(active["generation"]) is not int
            or active["generation"] != options.generation
            or active["deployment"] != options.deployment
            or (active["previous"] is not None and not _digest(active["previous"]))
        ):
            raise LaunchError
        if not _digest(options.deployment):
            raise LaunchError
        _starting_transition(lifecycle, options)
        deployments = lifecycle / "deployments"
        _directory(deployments)
        runtime = deployments / options.deployment
        _directory(runtime)
        encoded = _private_bytes(runtime / "runtime.json")
        if hashlib.sha256(encoded).hexdigest() != options.deployment:
            raise LaunchError
        manifest = json.loads(encoded)
        worker = runtime / "worker.py"
        files = manifest.get("files") if isinstance(manifest, dict) else None
        if (
            not isinstance(manifest, dict)
            or set(manifest) != {"schema", "core_version", "python", "python_sha256", "ui_sha256", "files"}
            or type(manifest["schema"]) is not int
            or manifest["schema"] != 1
            or manifest["python"] != sys.executable
            or not _digest(manifest["python_sha256"])
            or manifest.get("core_version") != options.version
            or manifest.get("ui_sha256") != options.ui_sha256
            or not isinstance(files, dict)
            or files.get("worker.py") != options.worker_sha256
            or Path(options.worker) != worker
            or hashlib.sha256(_private_bytes(worker)).hexdigest() != options.worker_sha256
        ):
            raise LaunchError
        return ownership(profile)
    finally:
        os.close(lock)


def bind_listener(port: int) -> tuple[socket.socket, socket.socket]:
    """Retain a non-listening wildcard reservation and serve only loopback, allowing immediate owned reopen.

    Reuse is required for the stable origin after TCP TIME_WAIT. On Darwin loopback reuse alone could overlap an
    existing wildcard listener, so reserve that exact wildcard address first; this descriptor never listens.

    Raises:
        OSError: If any foreign listener owns the selected address or either binding fails.
    """
    reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        reservation.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        reservation.bind(("0.0.0.0", port))  # noqa: S104 - reservation only; never listens or receives network traffic
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", int(reservation.getsockname()[1])))
        listener.listen(128)
    except OSError:
        listener.close()
        reservation.close()
        raise
    return listener, reservation


def main() -> int:
    """Retain the exclusive socket across a bounded worker crash/restart lifecycle.

    Raises:
        LaunchError: If private profile or worker bytes do not match the launch record.
        ValueError: If the launch is not JSON.
        OSError: If another owner holds the requested listener or profile.
    """
    options = configuration()
    worker_path = Path(str(options.worker))
    profile = Path(options.profile)
    lock = activated_ownership(options)
    closed = threading.Event()

    def parent_closed() -> None:
        # The first launch record is consumed. Any further bytes are invalid; EOF revokes the controller lease.
        os.read(sys.stdin.fileno(), 1)
        closed.set()

    threading.Thread(target=parent_closed, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: closed.set())
    signal.signal(signal.SIGINT, lambda *_: closed.set())
    token = secrets.token_urlsafe(32)
    listener, reservation = bind_listener(options.port)
    port = int(listener.getsockname()[1])
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "LANG", "LC_ALL", "TMPDIR", "HOME", "SSL_CERT_FILE", "SSL_CERT_DIR"}
    }
    environment.update(
        {
            "HEINZEL_UI_RESUME": f"{listener.fileno()}:{token}",
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HZ_CONFIG_HOME": str(profile / "config"),
            "HZ_DATA_DIR": str(profile / "data"),
            "HZ_STATE_DIR": str(profile / "state"),
            "HZ_CACHE_DIR": str(profile / "cache"),
            "HZ_LOG_DIR": str(profile / "logs"),
        }
    )
    worker: subprocess.Popen[bytes] | None = None
    starts: list[float] = []
    reported: str | None = None
    try:
        while not closed.is_set():
            if worker is None or worker.poll() is not None:
                if worker is not None and worker.stdin is not None:
                    worker.stdin.close()
                starts = [started for started in starts if time.monotonic() - started < CRASH_WINDOW]
                if len(starts) >= MAX_STARTS:
                    announce("failed", nonce=options.nonce, reason="backend-crash-loop")
                    break
                starts.append(time.monotonic())
                worker = subprocess.Popen(  # noqa: S603 - checked deployment worker with inherited owned descriptor, no shell
                    [sys.executable, "-I", "-S", "-B", str(worker_path)],
                    cwd=profile,
                    env=environment,
                    stdin=subprocess.PIPE,
                    stdout=sys.stderr,
                    stderr=sys.stderr,
                    pass_fds=(listener.fileno(),),
                    start_new_session=True,
                )
                reported = None
            identity = probe(port, token, str(options.version), str(options.ui_sha256))
            if closed.is_set():
                break
            if identity is not None and identity["instance"] != reported:
                reported = str(identity["instance"])
                announce(
                    "ready",
                    nonce=options.nonce,
                    instance=reported,
                    version=options.version,
                    ui_sha256=options.ui_sha256,
                    url=f"http://127.0.0.1:{port}/#token={token}",
                )
            closed.wait(2 if reported else 0.2)
    finally:
        if worker is not None and worker.poll() is None:
            graceful = stop_worker(worker)
            announce("stopped", nonce=options.nonce, graceful=graceful)
        listener.close()
        reservation.close()
        os.close(lock)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError):
        # Raw paths, credentials and plugin diagnostics do not belong in the controller protocol.
        announce("failed", reason="owned-backend-unavailable")
        raise SystemExit(1) from None
