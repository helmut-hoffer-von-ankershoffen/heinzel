"""The Google Workspace CLI (`gws`) as the backend: run it safely, parse its JSON, page, retry, explain failures.

`gws` signs in with Google (`gws auth login`), keeps its own credentials and talks to Google's APIs; this plugin never
sees a token. It runs `gws <service> <resource> <method> --params <JSON> --format json`: an argument list, never a
shell string, with the executable resolved to an absolute path (`google.gws_path`, which no project hz.toml can set,
or `gws` on an absolute PATH entry), an environment without Heinzelmännchen's credentials, the root directory as its
working directory (gws reads a `.env` from where it runs, which a project could otherwise ship), a timeout that kills
its process group, and at most `MAX_BYTES` of output kept. Every failure becomes a `HeinzelError` that says what to
do; text from `gws` passes `clean_text`, token-shaped strings are redacted whole before anything is cut (ADR 0036).
"""

import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, cast

from heinzel import HeinzelError, clean_text
from heinzel_google._models import GoogleSettings

MAX_BYTES = 10_000_000
"""Largest output read from one `gws` run: a long document, a page of 250 events or a spreadsheet stays below it."""
MAX_STDERR = 65_536
"""Bytes of `gws`'s diagnostics kept."""
MAX_SAID = 300
"""Characters of an error text shown, cut after redaction."""
MAX_PAGES = 100
"""Pages followed for one call before asking for a narrower window."""
BUDGET = 25_000_000
"""Bytes read for one call, all pages together, before asking for a narrower window (ADR 0028)."""
RETRIES = 3
"""Times a rate-limited request is retried, after 1, 2 and 4 seconds."""
GRACE = 2.0
"""Seconds a timed-out `gws` gets between SIGTERM and SIGKILL."""
SCOPES = (
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
)
"""The read-only scopes the adapters need; `LOGIN` asks `gws` for exactly these."""
MAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
"""Gmail's read-only scope, asked for only when `mail` is read from Google: Google classes it as restricted."""
LOGIN = f"gws auth login --scopes {','.join(SCOPES)}"
LOGIN_WITH_MAIL = f"gws auth login --scopes {','.join((*SCOPES, MAIL_SCOPE))}"
INSTALL = "npm install -g @googleworkspace/cli (or set google.gws_path to the gws executable)"
MIN_VERSION = (0, 22, 0)
"""The oldest `gws` this plugin was written and tested against (ADR 0035): its JSON and exit codes are no contract."""
_VERSION = re.compile(r"\bgws (\d+)\.(\d+)\.(\d+)")
PASSED = frozenset(
    {
        "PATH", "HOME", "USER", "LOGNAME", "LANG", "TMPDIR", "TERM",
        "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",  # gws's keyring on Linux
        "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "SYSTEMROOT",
        "GOOGLE_WORKSPACE_PROJECT_ID",
    }
)  # fmt: skip
"""Variables `gws` gets (ADR 0035): an allowlist, plus `LC_*` and gws's own `GOOGLE_WORKSPACE_CLI_*`; never anything
else, so neither `HZ_CREDENTIAL_*` nor an unrelated secret in the environment reaches it."""


def environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment `gws` runs with: the allowed variables of `source` (the current environment by default)."""
    variables = os.environ if source is None else source
    return {
        key: value
        for key, value in variables.items()
        if key in PASSED or key.startswith(("LC_", "GOOGLE_WORKSPACE_CLI_"))
    }


SETUP = "hz google setup"
CLIENT = "gws auth setup (needs gcloud), or create a Desktop OAuth client as gws's README describes"
NEUTRAL = "/"
"""The working directory of every `gws` run: nowhere a project can put a `.env`."""
_SECRETS = re.compile(r"ya29\.[\w.-]+|1//[\w.-]{20,}|GOCSPX-[\w-]+|eyJ[\w-]{5,}\.[\w-]+\.[\w-]+")
"""Access tokens, refresh tokens, client secrets and JWTs, in case `gws` or Google ever echo one."""
_RATE = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "RATE_LIMIT_EXCEEDED", "quotaExceeded"})
_SCOPE = frozenset({"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"})
_DISABLED = frozenset({"accessNotConfigured", "SERVICE_DISABLED"})


@dataclass(frozen=True, slots=True)
class Process:
    """How one `gws` run ended: its exit code (None if it was stopped at the timeout) and its output, cut."""

    exit_code: int | None
    stdout: bytes
    stderr: bytes = b""
    truncated: bool = False
    """Whether stdout passed `MAX_BYTES` and was cut."""


class _Capture:
    """Drains a pipe in a thread, keeping at most `limit` bytes."""

    def __init__(self, stream: IO[bytes], limit: int) -> None:
        self.chunks: list[bytes] = []
        self.size = 0
        self._limit = limit
        self._thread = threading.Thread(target=self._drain, args=(stream,), daemon=True)
        self._thread.start()

    def _drain(self, stream: IO[bytes]) -> None:
        with stream:
            while chunk := stream.read(65536):
                if self.size < self._limit:
                    self.chunks.append(chunk[: self._limit - self.size])
                self.size += len(chunk)

    def data(self) -> bytes:
        self._thread.join(GRACE)
        return b"".join(self.chunks)

    @property
    def draining(self) -> bool:
        return self._thread.is_alive()


def _signal_group(pid: int, signum: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):  # the group may be gone already
        os.killpg(pid, signum)


class Gws:
    """Starts `gws` and waits; the one place that runs a process or sleeps. Tests replace it through the kernel."""

    def run(self, executable: str, arguments: Sequence[str], timeout: float) -> Process:
        """Run `executable` with `arguments` in its own session; past `timeout` its process group is stopped.

        Raises:
            OSError: If the executable can't be started.
        """
        process = subprocess.Popen(  # noqa: S603 - an argument list, the executable resolved to an absolute path
            [executable, *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment(),
            cwd=NEUTRAL,
            start_new_session=True,
        )
        stdout = _Capture(cast("IO[bytes]", process.stdout), MAX_BYTES + 1)
        stderr = _Capture(cast("IO[bytes]", process.stderr), MAX_STDERR)
        exit_code: int | None
        try:
            exit_code = process.wait(timeout)
        except subprocess.TimeoutExpired:
            exit_code = None
            _signal_group(process.pid, signal.SIGTERM)
            try:
                process.wait(GRACE)
            except subprocess.TimeoutExpired:
                _signal_group(process.pid, signal.SIGKILL)
                process.wait()
        _signal_group(process.pid, signal.SIGKILL)  # whatever it left running
        out = stdout.data()
        cut = len(out) > MAX_BYTES or stdout.draining  # still draining: something it started holds the pipe
        return Process(exit_code, out[:MAX_BYTES], stderr.data(), truncated=cut)

    def login(self, executable: str, arguments: Sequence[str], timeout: float) -> int | None:
        """Run `gws auth login` with the terminal, so the person sees its link and prompts; its exit code.

        None if it was still running after `timeout`.

        Raises:
            OSError: If the executable can't be started.
        """
        try:
            return subprocess.run(  # noqa: S603 - an argument list, the executable resolved to an absolute path
                [executable, *arguments], env=environment(), cwd=NEUTRAL, check=False, timeout=timeout
            ).returncode
        except subprocess.TimeoutExpired:
            return None

    def sleep(self, seconds: float) -> None:
        """Wait `seconds` before retrying after a rate limit."""
        time.sleep(seconds)


def redact(text: str) -> str:
    """`text` cleaned of control and bidirectional characters, token-shaped strings replaced whole, then cut."""
    return _SECRETS.sub("***", clean_text(text))[:MAX_SAID]


class _TooDeep:
    """JSON nested deeper than Python's parser goes."""


TOO_DEEP = _TooDeep()


def _parse(data: bytes) -> object:
    try:
        return cast("object", json.loads(data))
    except RecursionError:
        return TOO_DEEP
    except ValueError:
        return None


def _on_path(name: str) -> str | None:
    """`name` on the absolute entries of PATH only: a relative entry would let the working directory choose."""
    entries = [entry for entry in os.get_exec_path() if Path(entry).is_absolute()]
    return shutil.which(name, path=os.pathsep.join(entries)) if entries else None


def _error(data: object) -> dict[str, Any]:
    error = cast("dict[str, Any]", data).get("error") if isinstance(data, dict) else None
    return cast("dict[str, Any]", error) if isinstance(error, dict) else {}


def _reasons(error: Mapping[str, Any]) -> set[str]:
    """The reasons given: `gws`'s `reason`, and Google's `errors[].reason` and `details[].reason`."""
    found = {str(error["reason"])} if error.get("reason") else set()
    for key in ("errors", "details"):
        entries = error.get(key)
        if isinstance(entries, list):
            found |= {
                str(cast("dict[str, Any]", e)["reason"])
                for e in cast("list[object]", entries)
                if isinstance(e, dict) and cast("dict[str, Any]", e).get("reason")
            }
    return found


class GoogleSession:
    """Reads Google's APIs through `gws` as the user `gws` signed in; one per kernel."""

    def __init__(self, settings: GoogleSettings, gws: Gws) -> None:
        """Create the session; the kernel injects the settings and the runner."""
        self._settings = settings
        self._gws = gws
        self._checked: str | None = None  # the executable whose version was checked

    def executable(self) -> str:
        """The absolute path of `gws`: `google.gws_path`, or `gws` on PATH.

        Raises:
            HeinzelError: If neither names an executable file, with the fix.
        """
        configured = self._settings.gws_path
        found = configured if configured is not None else _on_path("gws")
        if found is None or not Path(found).is_file() or not os.access(found, os.X_OK):
            where = f"google.gws_path ({configured})" if configured else "PATH"
            msg = f"The Google Workspace CLI (gws) is not found on {where}; install it: {INSTALL}."
            raise HeinzelError(msg)
        return str(Path(found).absolute())  # symlinks stay: a launcher may need its own name

    def get(
        self, api: str, command: Sequence[str], params: Mapping[str, object], subject: str
    ) -> tuple[dict[str, Any], int]:
        """Run `gws <command> --params <params>` and return the JSON object it prints, and its size in bytes.

        Args:
            api: The API's name for messages, e.g. `Google Docs`.
            command: Service, resource and method, e.g. `("docs", "documents", "get")`.
            params: The method's parameters.
            subject: What was asked for, for messages, e.g. `document 1AbC`.

        Raises:
            HeinzelError: If `gws` is missing, not signed in, times out, prints too much, or Google refuses.
        """
        executable = self.executable()
        if self._checked != executable:
            self.version()
            self._checked = executable
        arguments = [*command, "--params", json.dumps(dict(params), separators=(",", ":")), "--format", "json"]
        waits = 0
        while True:
            try:
                process = self._gws.run(executable, arguments, self._settings.timeout)
            except OSError as exc:
                msg = f"Could not start gws for {subject}: {type(exc).__name__}. Check google.gws_path: {INSTALL}."
                raise HeinzelError(msg) from None
            if process.exit_code is None:
                msg = f"gws did not answer {subject} within {self._settings.timeout:g} seconds; retry, or raise "
                msg += "google.timeout."
                raise HeinzelError(msg)
            if process.truncated:
                msg = f"{api}'s answer for {subject} is larger than {MAX_BYTES:,} bytes."
                raise HeinzelError(msg)
            data = _parse(process.stdout)
            if data is TOO_DEEP:
                msg = f"{api}'s answer for {subject} nests too deeply to read."
                raise HeinzelError(msg)
            if process.exit_code == 0 and isinstance(data, dict) and "error" not in data:
                return cast("dict[str, Any]", data), len(process.stdout)
            if process.exit_code == 0 and not (isinstance(data, dict) and "error" in data):
                msg = f"gws answered {subject} with something other than a JSON object; is it the Google Workspace CLI?"
                raise HeinzelError(msg)
            error = _error(data)
            limited = _reasons(error) & _RATE or error.get("code") == 429  # noqa: PLR2004 - Too Many Requests
            if limited and waits < RETRIES:
                self._gws.sleep(float(2**waits))
                waits += 1
                continue
            raise HeinzelError(self._problem(api, subject, process, error, waits))

    def pages(
        self, api: str, command: Sequence[str], params: Mapping[str, object], subject: str, items: str
    ) -> Iterator[tuple[list[dict[str, Any]], dict[str, Any]]]:
        """Each page of a list method, following `nextPageToken`, within `MAX_PAGES` and `BUDGET`.

        Stop iterating early to stop running `gws`.

        Yields:
            The objects under `items` of one page, and the whole page for its other fields.

        Raises:
            HeinzelError: As `get` does, and when the pages pass `MAX_PAGES` or `BUDGET`: narrow the window.
        """
        token: str | None = None
        spent = 0
        for _ in range(MAX_PAGES):
            page, size = self.get(api, command, {**params, "pageToken": token} if token else params, subject)
            spent += size
            if spent > BUDGET:
                msg = f"{api}'s answers for {subject} pass {BUDGET:,} bytes; ask for a narrower window."
                raise HeinzelError(msg)
            found = page.get(items, [])
            if not isinstance(found, list):
                msg = f"{api} answered {subject} without a list of {items}."
                raise HeinzelError(msg)
            objects = [cast("dict[str, Any]", item) for item in cast("list[object]", found) if isinstance(item, dict)]
            yield objects, page
            following = page.get("nextPageToken")
            if not isinstance(following, str) or not following:
                return
            token = following
        msg = f"{subject} has more than {MAX_PAGES} pages of results in {api}; ask for a narrower window."
        raise HeinzelError(msg)

    def version(self) -> str:
        """The version of `gws`, from `gws --version`, which needs no network.

        Raises:
            HeinzelError: If `gws` is missing, can't say its version, or is older than `MIN_VERSION`.
        """
        executable = self.executable()
        try:
            process = self._gws.run(executable, ["--version"], min(self._settings.timeout, 10.0))
        except OSError as exc:
            msg = f"Could not start gws: {type(exc).__name__}; {INSTALL}."
            raise HeinzelError(msg) from None
        found = _VERSION.search(process.stdout.decode(errors="replace"))
        if process.exit_code != 0 or found is None:
            msg = "gws --version gave no version this plugin understands; is gws the Google Workspace CLI?"
            raise HeinzelError(msg)
        number = tuple(int(part) for part in found.groups())
        shown = ".".join(found.groups())
        if number < MIN_VERSION:
            minimum = ".".join(map(str, MIN_VERSION))
            msg = f"gws {shown} is older than {minimum}, which this plugin needs; upgrade: {INSTALL}."
            raise HeinzelError(msg)
        return shown

    def signed_in(self) -> tuple[bool, str]:
        """Whether `gws` has credentials, from `gws auth status`, which reads local files only; and a detail.

        Not signed in, the detail says whether the OAuth client is missing too (`CLIENT` sets it up) or only the
        sign-in (`LOGIN`).

        Raises:
            HeinzelError: If `gws` is missing or its status can't be read.
        """
        executable = self.executable()
        shown = self.version()
        try:
            process = self._gws.run(executable, ["auth", "status"], min(self._settings.timeout, 10.0))
        except OSError as exc:
            msg = f"Could not start gws: {type(exc).__name__}; {INSTALL}."
            raise HeinzelError(msg) from None
        data = _parse(process.stdout)
        if process.exit_code != 0 or not isinstance(data, dict):
            msg = "gws auth status gave no answer this plugin understands; is gws the Google Workspace CLI?"
            raise HeinzelError(msg)
        status = cast("dict[str, Any]", data)
        method = str(status.get("auth_method") or "none")
        source = str(status.get("credential_source") or "none")
        if method == "none" and source == "none":
            client = status.get("client_config_exists") is not False
            return (
                False,
                f"gws {shown} is not signed in" if client else f"gws {shown} has no OAuth client and is not signed in",
            )
        return True, redact(f"gws {shown} signed in ({method}, from {source})")

    def _problem(  # noqa: PLR0911 - one message per kind of failure
        self, api: str, subject: str, process: Process, error: Mapping[str, Any], waits: int
    ) -> str:
        """What went wrong, for a person; never a token, and `gws`'s own words cleaned and cut."""
        said = redact(str(error.get("message") or process.stderr.decode(errors="replace").strip() or "no details"))
        code = error.get("code")
        reasons = _reasons(error)
        if reasons & _RATE or code == 429:  # noqa: PLR2004 - Too Many Requests
            return f"{api}'s rate limit is reached for {subject} after {waits} retries: retry in a minute."
        login = LOGIN_WITH_MAIL if api == "Gmail" else LOGIN
        if process.exit_code == 2 or code == 401:  # noqa: PLR2004 - gws's auth error, or Unauthorized
            return f"gws is not signed in to Google, or its sign-in expired ({said}). Sign in: {login}"
        if reasons & _SCOPE:
            return f"{api} refused {subject}: gws's sign-in lacks the read-only scope ({said}). Sign in: {login}"
        if reasons & _DISABLED:
            return f"{api} is not enabled in the Google Cloud project of gws's OAuth client ({said}); enable it there."
        if code == 404:  # noqa: PLR2004 - Not Found
            return f"{api} has no {subject}, or the signed-in account can't see it: check the link, or ask for access."
        if code == 403:  # noqa: PLR2004 - Forbidden
            return f"{api} refused {subject} ({said}); the signed-in account may lack access to it."
        if process.exit_code == 4:  # noqa: PLR2004 - gws could not fetch the API's discovery document
            return f"gws could not load {api}'s API description ({said}); check the network and retry."
        return f"gws failed on {subject} (exit {process.exit_code}): {said}"
