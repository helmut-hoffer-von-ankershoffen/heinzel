"""The one place that starts a process: the GitHub CLI `gh`, with an argument list, a deadline and bounded output.

`gh` owns authentication; the plugin never sees a token and never runs a subcommand that prints one (ADR 0035). The
child gets an allowlisted environment (never `HZ_CREDENTIAL_*`, ADR 0030, nor `GH_DEBUG` or `GODEBUG`, which print
request headers), no prompts, no standard input and the home directory as its working directory. It runs in its own
process group, so stopping it, with SIGTERM and then SIGKILL, also stops anything it started. Tests replace this
service through the kernel (`kernel.bind(Gh, fake)`) or put a fake `gh` first on `PATH`.
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

from heinzel import HeinzelError
from heinzel_github._models import GITHUB_HOST, GithubSettings

MAX_BYTES = 10 * 1024 * 1024
"""Largest standard output read; a page of 100 pull requests, which embed their repositories, is a few megabytes."""
MAX_ERROR_BYTES = 64 * 1024
"""Standard error kept: its last bytes, where the error is; the rest is read and dropped, so gh never blocks."""
GRACE = 1.0
"""Seconds the pipes may stay open after gh exited, or after SIGTERM, before its process group is killed."""
VERSION_TIMEOUT = 10.0
MAX_HOSTS_LINES = 10_000
"""Lines of gh's hosts file read for the login; a file with one login per host is a few lines each."""
MINIMUM_VERSION = (2, 40, 0)
"""The gh release whose `gh api` flags (`--hostname`, `--method`, `--header`, `--raw-field`) the adapter relies on."""
INSTALL = "https://cli.github.com"
ENVIRONMENT = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "TMPDIR",
    "TZ",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "XDG_CONFIG_HOME",  # where gh keeps its configuration and hosts file, when set
    "XDG_STATE_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",  # with the D-Bus address: the Secret Service, if gh keeps its login there
    "DBUS_SESSION_BUS_ADDRESS",
    "GH_CONFIG_DIR",
    "GH_TOKEN",  # gh's own logins from the environment: gh reads them, the plugin never does
    "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
)
"""Variables gh gets from the caller's environment; also `LC_*` and the proxy variables. `PATH` keeps its absolute
entries only."""
FIXED = {
    "GH_PROMPT_DISABLED": "1",
    "GH_NO_UPDATE_NOTIFIER": "1",
    "GH_NO_EXTENSION_UPDATE_NOTIFIER": "1",
    "GH_SPINNER_DISABLED": "1",
    "GH_PAGER": "",
    "NO_COLOR": "1",
}
"""Set for every call: never interactive, no pager, no colours, no update notices."""
_PROXIES = frozenset({"HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "ALL_PROXY"})
_VERSION = re.compile(r"\bgh version v?(\d{1,4})\.(\d{1,4})\.(\d{1,6})")
_HOST_KEY = re.compile(r"^([A-Za-z0-9.-]+):\s*$")
_CHUNK = 65_536
_POSIX = os.name == "posix"


def login_fix(host: str) -> str:
    """The command that logs `gh` in to `host`."""
    return f"gh auth login --hostname {host}"


def version_text(found: tuple[int, int, int]) -> str:
    """`2.40.0` for `(2, 40, 0)`."""
    return ".".join(str(part) for part in found)


@dataclass(frozen=True, slots=True)
class GhResult:
    """How a `gh` call ended: exit status (None: stopped at the deadline), stdout, the end of stderr."""

    returncode: int | None
    stdout: bytes
    stderr: str
    truncated: bool = False
    """True when stdout passed `MAX_BYTES` and gh was stopped."""


def absolute_path(search: str) -> str:
    """`search`, a `PATH` value, with its relative entries (`.`, empty, `bin`) left out."""
    return os.pathsep.join(entry for entry in search.split(os.pathsep) if entry and Path(entry).is_absolute())


def environment(host: str, source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Gh's environment: the allowlisted variables of `source` (the process's), pinned to `host`, never interactive."""
    variables = os.environ if source is None else source
    kept = {
        name: value
        for name, value in variables.items()
        if name in ENVIRONMENT or name.upper() in _PROXIES or name.startswith("LC_")
    }
    if "PATH" in kept:
        kept["PATH"] = absolute_path(kept["PATH"])
    return kept | FIXED | {"GH_HOST": host}


def parse_version(text: str) -> tuple[int, int, int] | None:
    """The version in `gh --version` output, `gh version 2.103.2 (2026-10-23)`; None if there is none."""
    found = _VERSION.search(text)
    return (int(found[1]), int(found[2]), int(found[3])) if found else None


def hosts_file(source: Mapping[str, str] | None = None) -> Path:
    """Where gh keeps its logins per host: `$GH_CONFIG_DIR`, `$XDG_CONFIG_HOME/gh` or `~/.config/gh`, `hosts.yml`."""
    variables = os.environ if source is None else source
    if directory := variables.get("GH_CONFIG_DIR"):
        return Path(directory) / "hosts.yml"
    if config := variables.get("XDG_CONFIG_HOME"):
        return Path(config) / "gh" / "hosts.yml"
    return Path.home() / ".config" / "gh" / "hosts.yml"


def _token_variables(host: str) -> tuple[str, ...]:
    if host == GITHUB_HOST or host.endswith(".ghe.com"):
        return ("GH_TOKEN", "GITHUB_TOKEN")
    return ("GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")


def _signal(process: subprocess.Popen[bytes], number: int) -> None:
    """Send `number` to gh's process group (to gh alone where there are none)."""
    try:
        if _POSIX:
            os.killpg(process.pid, number)
        else:  # pragma: no cover - Windows has no process groups to signal this way
            process.kill()
    except (ProcessLookupError, PermissionError):  # already gone
        pass


def _drain(stream: io.BufferedIOBase, sink: bytearray, limit: int, on_overflow: threading.Event | None) -> None:
    """Read `stream` until EOF, then close it; the reader owns it, so nothing closes it under a blocked read.

    With `on_overflow`, keep the first `limit` + 1 bytes, then set it and stop reading; without, keep the last `limit`
    bytes and read on, so the writer never blocks on a full pipe.
    """
    with stream:
        while chunk := stream.read1(_CHUNK):
            if on_overflow is None:
                sink.extend(chunk)
                del sink[:-limit]
                continue
            sink.extend(chunk[: max(0, limit + 1 - len(sink))])
            if len(sink) > limit:
                on_overflow.set()
                return


def _join(readers: list[threading.Thread], seconds: float) -> None:
    """Wait for the readers, `seconds` in all."""
    until = time.monotonic() + seconds
    for reader in readers:
        reader.join(max(0.0, until - time.monotonic()))


def _wait(
    process: subprocess.Popen[bytes], readers: list[threading.Thread], overflow: threading.Event, deadline: float
) -> bool:
    """Wait until gh exited and its output is read, or the output overflowed; False if the deadline passed."""
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


class Gh:
    """Runs `gh` for the adapter, and says where it is, which version it is and whether it has a login, offline."""

    def __init__(self, settings: GithubSettings) -> None:
        """Run the gh the settings name; the kernel injects them."""
        self._settings = settings
        self._versions: dict[str, tuple[int, int, int] | None] = {}

    def executable(self) -> str | None:
        """The absolute path of `github.gh`, looked up on `PATH` when it is a name; None if there is none.

        Relative `PATH` entries (`.`) are skipped, so a repository can't put its own `gh` first.
        """
        found = shutil.which(self._settings.gh, path=absolute_path(os.environ.get("PATH", os.defpath)))
        return found if found is not None and Path(found).is_absolute() else None

    def _require(self) -> str:
        if (path := self.executable()) is None:
            msg = (
                f"The GitHub CLI `{self._settings.gh}` is not on PATH; install it from {INSTALL}, then run "
                f"`{login_fix(self._settings.host)}`."
            )
            raise HeinzelError(msg)
        return path

    def version(self) -> tuple[int, int, int] | None:
        """Gh's version, from `gh --version`; None if it names none.

        Asked once per executable path; a version below `MINIMUM_VERSION` is asked again next time, so an upgrade
        counts without restarting.

        Raises:
            HeinzelError: If gh is not on `PATH`, can't be started, or doesn't answer in time.
        """
        path = self._require()
        if path not in self._versions:
            limit = min(VERSION_TIMEOUT, self._settings.timeout)
            done = self._execute(path, ["--version"], limit)
            if done.returncode is None:
                msg = f"`{path} --version` did not answer within {limit:g} seconds; reinstall gh ({INSTALL})."
                raise HeinzelError(msg)
            if done.returncode != 0:
                msg = f"`{path} --version` failed (exit code {done.returncode}); reinstall gh ({INSTALL})."
                raise HeinzelError(msg)
            found = parse_version(done.stdout.decode(errors="replace") + done.stderr)
            if found is not None and found < MINIMUM_VERSION:
                return found
            self._versions[path] = found
        return self._versions[path]

    def logged_in(self) -> bool:
        """Whether gh has a login for the configured host: its hosts file names it, or a token variable gh reads is set.

        Reads only the name of the host in gh's own file, never a token; GitHub is not asked, so a login that
        expired is found by the first call, which then names `gh auth login`.
        """
        host = self._settings.host
        if any(os.environ.get(name) for name in _token_variables(host)):
            return True
        try:
            with hosts_file().open(encoding="utf-8", errors="replace") as lines:
                # Line by line, top-level keys only: a token gh stored here in plain text is never kept or matched.
                for number, line in enumerate(lines):
                    if number >= MAX_HOSTS_LINES:
                        return False
                    if (found := _HOST_KEY.match(line)) and found[1].lower() == host:
                        return True
        except OSError:
            return False
        return False

    def run(self, args: list[str], *, host: str, timeout: float) -> GhResult:
        """Run `gh <args>` for `host`; past `timeout` seconds, or past `MAX_BYTES` of output, it is stopped.

        Raises:
            HeinzelError: If gh is not on `PATH`, can't be started, or is older than `MINIMUM_VERSION`.
        """
        path = self._require()
        found = self.version()
        if found is not None and found < MINIMUM_VERSION:
            msg = (
                f"{path} is gh {version_text(found)}; the GitHub adapter needs {version_text(MINIMUM_VERSION)} or "
                f"later: upgrade it ({INSTALL})."
            )
            raise HeinzelError(msg)
        return self._execute(path, args, timeout, host)

    def _execute(self, path: str, args: Sequence[str], timeout: float, host: str | None = None) -> GhResult:
        deadline = time.monotonic() + timeout
        out, err = bytearray(), bytearray()
        overflow = threading.Event()
        try:
            # Not `with Popen`: its exit closes the pipes, which blocks while a reader waits on one that a descendant
            # of gh still holds. The readers close their own pipes.
            process = subprocess.Popen(  # noqa: S603 - an argument list, the executable resolved with shutil.which
                [path, *args],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=Path.home(),
                env=environment(host or self._settings.host),
                start_new_session=_POSIX,
            )
        except (OSError, RuntimeError) as exc:  # RuntimeError: no home directory
            msg = f"Could not start the GitHub CLI {path} ({type(exc).__name__}); reinstall it from {INSTALL}."
            raise HeinzelError(msg) from None
        readers = [
            threading.Thread(target=_drain, args=(cast("io.BufferedIOBase", stream), sink, limit, flag), daemon=True)
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
        # Only while gh runs, or something of its group holds the pipes: then its group id is still taken.
        if process.poll() is None or any(reader.is_alive() for reader in readers):
            _signal(process, signal.SIGKILL)
        process.wait()
        _join(readers, GRACE)
        timed_out = not finished or any(reader.is_alive() for reader in readers)  # a descendant that left the group
        return GhResult(
            returncode=None if timed_out else process.returncode,
            stdout=bytes(out),
            stderr=bytes(err).decode(errors="replace"),
            truncated=overflow.is_set(),
        )
