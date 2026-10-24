"""The one place that starts a process: Atlassian's CLI `acli`, with an argument list, a deadline and bounded output.

acli owns authentication; the plugin never sees a token (ADR 0035). The child gets an allowlisted environment
(never `HZ_CREDENTIAL_*`, ADR 0030, nor the rest of the caller's), colours off, no standard input and the home
directory as its working directory, so a repository `hz` runs in can't hand it a configuration file. It runs in its
own process group, so stopping it, with SIGTERM and then SIGKILL, also stops anything it started.
"""

import io
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from heinzel_atlassian._models import AtlassianSettings

MAX_BYTES = 10 * 1024 * 1024
"""Largest standard output read; a window of a thousand issues, or one long Confluence page, stays well below it."""
MAX_ERROR_BYTES = 64 * 1024
"""Standard error kept: its last bytes, where the error is; the rest is read and dropped, so acli never blocks."""
GRACE = 1.0
"""Seconds the pipes may stay open after acli exited, or after SIGTERM, before its process group is killed."""
VERSION_TIMEOUT = 10.0
MINIMUM_VERSION = (1, 3, 39)
"""The acli release whose commands and flags the adapter was written against."""
ENVIRONMENT = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "TMPDIR", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR")
"""Variables acli gets from the caller's environment; also `LC_*`, `ACLI_*` and the proxy variables."""
_PREFIXES = ("LC_", "ACLI_")
_PROXIES = frozenset({"HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "ALL_PROXY"})
_VERSION = re.compile(r"\bacli version v?(\d{1,4})\.(\d{1,4})\.(\d{1,6})")
_CHUNK = 65_536
_POSIX = os.name == "posix"


@dataclass(frozen=True, slots=True)
class Completed:
    """What acli did: its exit status, at most `MAX_BYTES` + 1 of standard output, the end of standard error."""

    returncode: int
    stdout: bytes
    stderr: bytes
    overflow: bool
    """True when standard output passed `MAX_BYTES` and acli was stopped."""


class AcliTimeoutError(Exception):
    """acli did not finish within the deadline and was stopped."""


def environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment acli runs with: the allowlisted variables of `source` (the process's), and colours off."""
    variables = os.environ if source is None else source
    kept = {
        name: value
        for name, value in variables.items()
        if name in ENVIRONMENT or name.upper() in _PROXIES or name.startswith(_PREFIXES)
    }
    return kept | {"NO_COLOR": "1"}


def executable(settings: AtlassianSettings) -> str | None:
    """The absolute path of `atlassian.acli`, looked up on `PATH` when it is a name; None if there is none.

    A relative `PATH` entry (`.`) doesn't count, so a repository can't put its own `acli` first.
    """
    found = shutil.which(settings.acli)
    return found if found is not None and Path(found).is_absolute() else None


def parse_version(text: str) -> tuple[int, int, int] | None:
    """The version in `acli --version` output, `acli version 1.3.39-stable`; None if there is none."""
    found = _VERSION.search(text)
    return (int(found[1]), int(found[2]), int(found[3])) if found else None


def _signal(process: subprocess.Popen[bytes], number: int) -> None:
    """Send `number` to acli's process group (to acli alone where there are none)."""
    try:
        if _POSIX:
            os.killpg(process.pid, number)
        else:  # pragma: no cover - Windows has no process groups to signal this way
            process.kill()
    except (ProcessLookupError, PermissionError):  # already gone
        pass


def _drain(stream: io.BufferedIOBase, sink: bytearray, limit: int, on_overflow: threading.Event | None) -> None:
    """Read `stream` until EOF.

    With `on_overflow`, keep the first `limit` + 1 bytes, then set it and stop reading; without, keep the last `limit`
    bytes and read on, so the writer never blocks on a full pipe.
    """
    while chunk := stream.read1(_CHUNK):
        if on_overflow is None:
            sink.extend(chunk)
            del sink[:-limit]
            continue
        sink.extend(chunk[: max(0, limit + 1 - len(sink))])
        if len(sink) > limit:
            on_overflow.set()
            return


class Acli:
    """Runs acli subcommands and returns what they printed, never more than the bounds."""

    def __init__(self, settings: AtlassianSettings) -> None:
        """Run the acli the settings name; the kernel injects them."""
        self._settings = settings
        self._version: tuple[int, int, int] | None = None

    def path(self) -> str | None:
        """The absolute path of the acli that runs, or None."""
        return executable(self._settings)

    def version(self) -> tuple[int, int, int] | None:
        """Acli's version, from `acli --version`, which needs no network; asked once. None if it says none.

        Raises:
            FileNotFoundError: If acli is not on `PATH` as an absolute path.
            AcliTimeoutError: If it doesn't answer in time.
            OSError: If it can't be started.
        """
        if self._version is None:
            done = self.run(["--version"], VERSION_TIMEOUT)
            self._version = parse_version((done.stdout + done.stderr).decode("utf-8", errors="replace"))
        return self._version

    def run(self, args: Sequence[str], timeout: float) -> Completed:
        """Run `acli <args>` without a shell or standard input, stopping it and its children after `timeout` seconds.

        Raises:
            FileNotFoundError: If acli is not on `PATH` as an absolute path.
            AcliTimeoutError: If it doesn't finish in time.
            OSError: If it can't be started.
        """
        path = self.path()
        if path is None:
            raise FileNotFoundError(self._settings.acli)
        deadline = time.monotonic() + timeout
        out, err = bytearray(), bytearray()
        overflow = threading.Event()
        with subprocess.Popen(  # noqa: S603 - an argument list, the executable resolved with shutil.which
            [path, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=Path.home(),
            env=environment(),
            start_new_session=_POSIX,
        ) as process:
            readers = [
                threading.Thread(
                    target=_drain, args=(cast("io.BufferedIOBase", stream), sink, limit, flag), daemon=True
                )
                for stream, sink, limit, flag in (
                    (process.stdout, out, MAX_BYTES, overflow),
                    (process.stderr, err, MAX_ERROR_BYTES, None),
                )
            ]
            for reader in readers:
                reader.start()
            finished = _wait(process, readers, overflow, deadline)
            if not finished:
                _signal(process, signal.SIGTERM)
                _join(readers, GRACE)
            _signal(process, signal.SIGKILL)  # also when acli exited: a child it left must not hold the pipes open
            process.wait()
            _join(readers, GRACE)
        if not finished:
            raise AcliTimeoutError(path)
        return Completed(process.returncode, bytes(out), bytes(err), overflow.is_set())


def _join(readers: list[threading.Thread], seconds: float) -> None:
    """Wait for the readers, `seconds` in all."""
    until = time.monotonic() + seconds
    for reader in readers:
        reader.join(max(0.0, until - time.monotonic()))


def _wait(
    process: subprocess.Popen[bytes], readers: list[threading.Thread], overflow: threading.Event, deadline: float
) -> bool:
    """Wait until acli exited and its output is read, or the output overflowed; False if the deadline passed."""
    while process.poll() is None:
        if overflow.is_set():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            process.wait(min(remaining, 0.05))
        except subprocess.TimeoutExpired:
            continue
    _join(readers, min(GRACE, max(0.0, deadline - time.monotonic())))
    return True
