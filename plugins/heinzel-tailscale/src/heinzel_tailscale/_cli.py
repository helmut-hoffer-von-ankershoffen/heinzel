"""Bounded Tailscale CLI calls; no shell, inherited debug settings or interactive login during status reads."""

import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from heinzel import HeinzelError

MAX_BYTES = 256 * 1024
TIMEOUT = 5.0
GRACE = 0.5
CHUNK = 65536
_ENVIRONMENT = frozenset({"PATH", "HOME", "USER", "LOGNAME", "LANG", "TMPDIR", "XDG_RUNTIME_DIR"})


@dataclass(frozen=True, slots=True)
class Result:
    """Bounded raw process output, retained only inside the adapter."""

    returncode: int
    stdout: bytes
    stderr: bytes


class TailscaleCli:
    """The ordinary user's installed CLI, with a fixed operation grammar and bounded process lifetime."""

    def executable(self) -> str | None:
        """Find the standard CLI on an absolute-only PATH; never install or execute it to detect availability."""
        found = shutil.which("tailscale", path=self.environment().get("PATH", ""))
        if found is None and sys.platform == "darwin":
            return shutil.which("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
        return found

    @staticmethod
    def environment() -> dict[str, str]:
        """Pass only the process environment the ordinary user's local Tailscale client needs."""
        inherited = os.environ
        env = {key: value for key, value in inherited.items() if key in _ENVIRONMENT or key.startswith("LC_")}
        env["PATH"] = os.pathsep.join(
            entry for entry in env.get("PATH", "").split(os.pathsep) if entry and Path(entry).is_absolute()
        )
        # The bundled macOS binary otherwise may open its GUI when launched by a background process.
        return env | {"NO_COLOR": "1", "TAILSCALE_BE_CLI": "1"}

    def json(self, arguments: Sequence[str]) -> object:
        """Read bounded JSON from an approved read-only CLI command.

        Raises:
            HeinzelError: If the CLI is missing, fails, times out or returns an unsupported response.
        """
        allowed = {("status", "--json", "--peers=false"), ("serve", "status", "--json")}
        if tuple(arguments) not in allowed:
            msg = "This Tailscale read does not match an approved command."
            raise HeinzelError(msg)
        result = self.run(arguments)
        if result.returncode:
            msg = "Tailscale could not report its state; open the Tailscale app and check its connection."
            raise HeinzelError(msg)
        try:
            return json.loads(result.stdout)
        except (ValueError, UnicodeError) as error:
            msg = "Tailscale returned an unsupported response; no sharing settings were changed."
            raise HeinzelError(msg) from error

    def run(self, arguments: Sequence[str]) -> Result:
        """Run the selected command with bounded output and reap its process group on every exit.

        Raises:
            HeinzelError: If the command is unavailable or exceeds its deadline/output limit.
        """
        executable = self.executable()
        if executable is None:
            msg = "Install Tailscale and sign in before sharing the web app."
            raise HeinzelError(msg)
        if os.name != "posix":
            msg = "This sharing adapter currently supports macOS and Linux."
            raise HeinzelError(msg)
        try:
            process = subprocess.Popen(  # noqa: S603 - resolved CLI, fixed argument lists, no shell
                [executable, *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=Path.home(),
                env=self.environment(),
                start_new_session=True,
                bufsize=0,
            )
        except OSError as error:
            msg = "Tailscale could not start; check the installed client and its permissions."
            raise HeinzelError(msg) from error
        try:
            return _read(process)
        finally:
            _stop(process)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


def _read(process: subprocess.Popen[bytes]) -> Result:
    output: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    until = time.monotonic() + TIMEOUT
    with selectors.DefaultSelector() as watcher:
        for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            if pipe is not None:
                os.set_blocking(pipe.fileno(), False)
                watcher.register(pipe, selectors.EVENT_READ, name)
        while watcher.get_map():
            remaining = until - time.monotonic()
            if remaining <= 0:
                msg = "Tailscale did not answer within five seconds; inspect its connection and try again."
                raise HeinzelError(msg)
            for key, _ in watcher.select(remaining):
                chunk = os.read(key.fd, CHUNK)
                if not chunk:
                    watcher.unregister(key.fileobj)
                    continue
                output[key.data].extend(chunk)
                if sum(map(len, output.values())) > MAX_BYTES:
                    msg = "Tailscale's response exceeded the size limit; no sharing changes were requested."
                    raise HeinzelError(msg)
        try:
            code = process.wait(max(0.0, until - time.monotonic()))
        except subprocess.TimeoutExpired as error:
            msg = "Tailscale did not stop within its deadline."
            raise HeinzelError(msg) from error
    return Result(code, bytes(output["stdout"]), bytes(output["stderr"]))


def _stop(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        # If the session leader exited and its process-group id was reused, never signal that foreign group.
        if process.poll() is None:
            process.terminate()
    try:
        process.wait(GRACE)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # The CLI may already have exited while one of its descendants ignored SIGTERM.
        # macOS may recycle the exited session leader's pgid; never signal a foreign group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            if process.poll() is None:
                process.kill()
        process.wait()
