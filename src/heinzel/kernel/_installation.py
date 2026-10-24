"""How Heinzelmännchen is installed, and the uv commands that change the installation.

Heinzelmännchen is meant to be installed with `uv tool install`. uv records the tool's requirements — Heinzelmännchen
plus every
`--with <plugin>` — in `uv-receipt.toml`, and `uv tool upgrade` keeps them, whereas packages put into the tool
environment any other way are dropped on the next upgrade (ADR 0006). So adding a plugin means reinstalling the tool
with one more `--with`, and updating Heinzelmännchen means reinstalling it with the same `--with`s. Outside a uv tool
environment (a development checkout) packages are installed with `uv pip install` instead.
"""

import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

from heinzel.kernel._credentials import subprocess_environment
from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._progress import Progress

RECEIPT_FILENAME = "uv-receipt.toml"
CORE_DISTRIBUTION = "heinzel"

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


COMMAND_TIMEOUT = 900  # seconds; a uv tool install that downloads a lot is slow, one that hangs is broken
HZ_TIMEOUT = 120


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run a command; a timeout becomes exit code 124 (as with timeout(1)), so callers handle it like any failure."""
    try:
        return subprocess.run(  # noqa: S603 - trusted parts, no shell
            command, capture_output=True, text=True, check=False, timeout=COMMAND_TIMEOUT, env=subprocess_environment()
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "", f"{command[0]} timed out after {COMMAND_TIMEOUT}s")


def requirement_from_receipt(entry: dict[str, Any]) -> str:
    """Turn a requirement from a uv tool receipt back into a PEP 508 string uv accepts on the command line."""
    name = str(entry["name"])
    if extras := entry.get("extras"):
        name = f"{name}[{','.join(extras)}]"
    if "git" in entry:
        parts = urlsplit(str(entry["git"]))
        query = parse_qs(parts.query)
        ref = next((query[key][0] for key in ("rev", "tag", "branch") if key in query), None)
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        fragment = f"#subdirectory={query['subdirectory'][0]}" if "subdirectory" in query else ""
        return f"{name} @ git+{url}{'@' + ref if ref else ''}{fragment}"
    for key in ("directory", "editable", "path"):
        if key in entry:
            return f"{name} @ {Path(entry[key]).resolve().as_uri()}"
    if "url" in entry:
        return f"{name} @ {entry['url']}"
    return f"{name}{entry.get('specifier', '')}"


class Installation:
    """The environment Heinzelmännchen runs in, and the commands that install into it. Injected by type."""

    def __init__(
        self,
        progress: Progress,
        prefix: Path | None = None,
        runner: Runner = _run,
        uv: str | None = None,
    ) -> None:
        """Create the installation; `progress` is injected, the rest is for tests.

        Args:
            progress: Shows that a command is running.
            prefix: Environment Heinzelmännchen runs in; defaults to `sys.prefix`.
            runner: Runs a command and returns the completed process.
            uv: Path to the uv executable; defaults to the one on `PATH`.
        """
        self._progress = progress
        self.prefix = prefix or Path(sys.prefix)
        self._runner = runner
        self._uv = uv or shutil.which("uv")

    @property
    def is_tool(self) -> bool:
        """Whether Heinzelmännchen runs from a `uv tool` environment."""
        return (self.prefix / RECEIPT_FILENAME).is_file()

    def requirements(self) -> list[str] | None:
        """Requirements of the uv tool environment, Heinzelmännchen first; None outside of one."""
        if not self.is_tool:
            return None
        data = tomllib.loads((self.prefix / RECEIPT_FILENAME).read_text(encoding="utf-8"))
        return [requirement_from_receipt(entry) for entry in data["tool"]["requirements"]]

    def tool_install_command(self, core: str, plugins: Sequence[str]) -> list[str]:
        """Reinstall the tool environment from `core` with `plugins`, on the Python Heinzelmännchen runs on."""
        python = f"{sys.version_info.major}.{sys.version_info.minor}"
        command = [self.uv, "tool", "install", "--force", "--python", python, core]
        for plugin in plugins:
            command += ["--with", plugin]
        return command

    def pip_command(self, action: str, spec: str) -> list[str]:
        """`uv pip install` or `uninstall` into the environment Heinzelmännchen runs in."""
        return [self.uv, "pip", action, "--python", sys.executable, spec]

    def upgrade_command(self) -> list[str]:
        """Upgrade Heinzelmännchen from where it was installed from, keeping plugins."""
        return [self.uv, "tool", "upgrade", CORE_DISTRIBUTION]

    def run(self, command: list[str], failure: str, activity: str) -> None:
        """Run a command with a progress indicator.

        Raises:
            HeinzelError: With `failure` and the command's output, if it fails.
        """
        with self._progress.task(activity):
            result = self._runner(command)
        if result.returncode != 0:
            msg = f"{failure}\n{result.stderr.strip() or result.stdout.strip()}"
            raise HeinzelError(msg)

    def hz(self, *args: str) -> str:
        """Run `hz` in a fresh interpreter and return its output; this process still has the old environment loaded.

        Raises:
            HeinzelError: If the command fails.
        """
        try:
            result = subprocess.run(  # noqa: S603 - our own interpreter and module
                [sys.executable, "-m", "heinzel", *args],
                capture_output=True,
                text=True,
                check=False,
                timeout=HZ_TIMEOUT,
                # A probe, not a person: no update check (ADR 0031) to wait for or to spend a request on.
                env=subprocess_environment() | {"HZ_UPDATE_CHECK": "false"},
            )
        except subprocess.TimeoutExpired as exc:
            msg = f"`hz {' '.join(args)}` did not finish within {HZ_TIMEOUT}s after the change."
            raise HeinzelError(msg) from exc
        if result.returncode != 0:
            msg = f"`hz {' '.join(args)}` failed after the change:\n{result.stderr}"
            raise HeinzelError(msg)
        return result.stdout

    @property
    def uv(self) -> str:
        """The uv executable.

        Raises:
            HeinzelError: If uv is not installed.
        """
        if not self._uv:
            msg = "uv is required to manage Heinzelmännchen's installation: https://docs.astral.sh/uv/"
            raise HeinzelError(msg)
        return self._uv
