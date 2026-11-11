"""Publish a live web instance over local owner-only control without writing its bearer to disk or logs."""

import json
import logging
import os
import re
import secrets
import stat
import sys
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from heinzel.kernel import (
    HeinzelError,
    PrivateControl,
    installation_mutation_pending,
    private_directory,
    private_read,
    private_socket_directory,
    private_write,
)

logger = logging.getLogger(__name__)
_MAX_ERRNO = 4095
SUPERVISED_LOG = "web.err.log"
"""Where an OS-supervised web server's stderr goes, in `hz.log_dir`: the service manager discards it otherwise."""
SUPERVISED_LOG_LIMIT = 1024 * 1024
"""Past this size the log moves to `web.err.log.1` when the server starts, so a crash loop can't fill the disk."""


def redirect_supervised_stderr(log_dir: Path) -> Path | None:
    """Send this process's stderr (fd 2) to an owner-only log file; None, leaving stderr as it was, if unsafe.

    A supervised server never announces its session URL (`WebPublication`), so stderr holds only warnings, startup
    failures and tracebacks: what diagnosing a service that keeps exiting needs. The service manager itself still
    discards both streams, so no output reaches an unrotated OS log.
    """
    path = log_dir / SUPERVISED_LOG
    try:
        private_directory(log_dir)
        try:
            info = path.lstat()
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISREG(info.st_mode) and info.st_size > SUPERVISED_LOG_LIMIT:
                path.replace(path.with_name(f"{SUPERVISED_LOG}.1"))
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            info = os.fstat(descriptor)
            owned = stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
            if not owned or info.st_mode & 0o077:
                return None
            sys.stderr.flush()
            os.dup2(descriptor, 2)
        finally:
            os.close(descriptor)
    except (OSError, HeinzelError):
        return None
    return path


class SupervisedWeb:
    """The live server's control lifetime, bound to its installed service generation and actual lifecycle."""

    def __init__(  # noqa: PLR0913 - explicit runtime identity and narrow callbacks
        self,
        directory: Path,
        *,
        generation: str,
        port: int,
        instance: str,
        version: str,
        url: str,
        restart: Callable[[], None],
    ) -> None:
        """Publish only after the server starts and only for its exact owned installation.

        Raises:
            HeinzelError: If the installed generation or port does not match.
        """
        record = TypeAdapter(dict[str, JsonValue]).validate_json(private_read(directory / "service.json"))
        if (
            re.fullmatch(r"[0-9a-f]{32}", generation) is None
            or record.get("generation") != generation
            or record.get("port") != port
        ):
            msg = "The running web service does not match its installed generation and port."
            raise HeinzelError(msg)
        prefix = record.get("prefix")
        if not isinstance(prefix, str) or not Path(prefix).is_absolute():
            msg = "The web service installation prefix could not be verified."
            raise HeinzelError(msg)
        self.directory = directory
        self.prefix = Path(prefix)
        name = f"ctl-{secrets.token_hex(6)}.sock"
        self.identity: dict[str, JsonValue] = {
            "pid": os.getpid(),
            "generation": generation,
            "port": port,
            "instance": instance,
        }
        self.record = json.dumps({"socket": name, **self.identity})

        def handle(command: str) -> dict[str, JsonValue]:
            if command not in {"status", "open", "restart"}:
                return {"error": "Unknown local web command."}
            response = {**self.identity, "version": version}
            if command == "open":
                response["url"] = url
            if command == "restart":
                if installation_mutation_pending(self.prefix, directory.parent):
                    response["error"] = "mutation-pending"
                    return response
                restart()
            return response

        self.control = PrivateControl(private_socket_directory(directory) / name, handle)
        try:
            private_write(directory / "runtime.json", self.record)
        except BaseException:
            self.control.close()
            raise

    def close(self) -> None:
        """Remove only this runtime's control endpoint and matching publication."""
        self.control.close()
        try:
            path = self.directory / "runtime.json"
            if private_read(path) == self.record:
                path.unlink()
        except (OSError, HeinzelError):
            pass


@dataclass(slots=True)
class WebPublication:
    """Select terminal announcement or private OS-service publication after the web listener starts."""

    directory: Path
    generation: str | None
    port: int
    instance: str
    version: str
    url: str
    restart: Callable[[], None]
    failed: Callable[[], None]
    control: SupervisedWeb | None = None

    def publish(self, announce: Callable[[str], None], *, browser: bool) -> None:
        """Publish after readiness; a failed private publication stops rather than exposing a service URL."""
        if self.generation is None:
            announce(self.url)
            if browser:
                webbrowser.open(self.url)
            return
        try:
            self.control = SupervisedWeb(
                self.directory,
                generation=self.generation,
                port=self.port,
                instance=self.instance,
                version=self.version,
                url=self.url,
                restart=self.restart,
            )
        except (OSError, ValueError, HeinzelError) as exc:
            # Publication can contain a session bearer: log fixed categories, never exception text or paths.
            category = "os" if isinstance(exc, OSError) else "value" if isinstance(exc, ValueError) else "ownership"
            number = exc.errno if isinstance(exc, OSError) else None
            errno = str(number) if type(number) is int and 1 <= number <= _MAX_ERRNO else "none"
            logger.warning("Owned web publication failed: category=%s errno=%s", category, errno)
            self.failed()

    def close(self) -> None:
        """Close only the publication owned by this server instance."""
        if self.control is not None:
            self.control.close()


def restart_when_settled(
    restart: Callable[[], None], *, prefix: Path, state: Path, supervised: bool
) -> Callable[[], None]:
    """Sample pending mutation gates for every supervised restart route, including the existing web API."""

    def request() -> None:
        if supervised and installation_mutation_pending(prefix, state):
            msg = "An installation change requires completion or recovery before restarting the web service."
            raise HeinzelError(msg)
        restart()

    return request
