"""Repository URLs hz may clone or push to, and the one way modules run `git` and `gh` on them.

A URL comes from a chat message or a manifest someone else wrote, and git treats some strings as commands: a leading
dash is an option, `ext::` runs a program, `fd::` reads a descriptor. Only plain https, ssh and scp-style addresses and
local repositories are accepted, with host and user names that can't start an option and never with a password in
them, and every command passes the URL after `--`.

Repository content may be someone else's, so every git command runs with the hardening below: no submodules (a hostile
`.gitmodules` would fetch and check out URLs of its choosing), no `ext::` transport, no LFS downloads, and none of the
caller's `GIT_DIR`-style variables, so `git -C <checkout>` acts on the checkout and nothing else.
"""

import contextlib
import os
import re
import shutil
import signal
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from heinzel.kernel._credentials import subprocess_environment
from heinzel.kernel._errors import HeinzelError

TIMEOUT = 300.0
"""Seconds a clone, push or `gh` call may take before it is stopped; the repositories hz works on are small."""
MAX_URL = 2048
_SCP = re.compile(r"^(?P<user>[A-Za-z0-9._-]+)@(?P<host>[A-Za-z0-9.-]+):(?P<path>[A-Za-z0-9._~/-]+)$")
_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")
_USER = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._-]*$")
_PATH = re.compile(r"^[A-Za-z0-9._~/@%+-]+$")
QUIET = {
    "GIT_TERMINAL_PROMPT": "0",
    "GH_PROMPT_DISABLED": "1",
    "GIT_ASKPASS": "",
    "SSH_ASKPASS": "",
    "GIT_LFS_SKIP_SMUDGE": "1",
}
"""Git and gh never wait for a password (there may be no terminal: MCP), and LFS never downloads what a repo names."""
GIT_HARDENING = (
    "-c",
    "submodule.recurse=false",
    "-c",
    "protocol.ext.allow=never",
    "-c",
    "protocol.file.allow=user",
)
"""Options every git command hz runs on a repository starts with."""
DROPPED = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
    }
)
"""Variables that would point `git -C <checkout>` at another repository, e.g. when hz runs inside a git hook."""
ALLOWED = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "TMPDIR",
        "TERM",
        "XDG_CONFIG_HOME",
        "SSH_AUTH_SOCK",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
        "https_proxy",
        "http_proxy",
        "no_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
)
ALLOWED_PREFIXES = ("LC_", "GIT_", "GH_", "GITHUB_")
"""What git and gh get of the environment (ADR 0035): the basics, ssh's agent, proxies, and their own variables."""
GRACE = 2.0
MAX_STDERR = 64 * 1024


def git_command(*parts: str | Path) -> list[str]:
    """A git command line with the hardening options first."""
    return ["git", *GIT_HARDENING, *(str(part) for part in parts)]


def check_repository_url(url: str) -> str:
    """The repository URL or local path, stripped, if it is one git may clone safely.

    Raises:
        ValueError: Saying why it isn't.
    """
    url = url.strip()
    if not url:
        msg = "the repository URL is empty"
        raise ValueError(msg)
    if len(url) > MAX_URL or any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in url):  # noqa: PLR2004 - DEL
        msg = "a repository URL is one line without spaces or control characters"
        raise ValueError(msg)
    if url.startswith("-"):
        msg = f"{url!r} starts with a dash, which git would read as an option"
        raise ValueError(msg)
    if "::" in url:
        msg = f"{url!r} names a git transport (`<transport>::`), which can run programs; use an https or ssh URL"
        raise ValueError(msg)
    if (match := _SCP.fullmatch(url)) is not None:
        if match["path"].startswith("-"):
            msg = f"the path of {url!r} starts with a dash"
            raise ValueError(msg)
        _check_host(url, match["host"], match["user"])
        return url
    if "://" in url:
        return _checked_url(url)
    if url.startswith(("/", "~")) and _PATH.fullmatch(url):
        return str(Path(url).expanduser())  # commands get argument lists, so no shell expands `~`
    msg = f"{url!r} isn't a repository URL; use https://host/owner/name.git, git@host:owner/name.git or a local path"
    raise ValueError(msg)


def _checked_url(url: str) -> str:
    """`url` if it is an https, ssh or file URL without a password.

    Raises:
        ValueError: Saying why it isn't.
    """
    parts = urlsplit(url)
    if parts.scheme not in {"https", "ssh", "file"}:
        msg = f"{url!r} uses {parts.scheme}://; use https://, ssh:// or a local path"
        raise ValueError(msg)
    if parts.password is not None or (parts.scheme == "https" and parts.username is not None):
        msg = "the URL holds a user, password or token; remove it, git asks your credential helper instead"
        raise ValueError(msg)
    if parts.scheme != "file":
        if not parts.hostname:
            msg = f"{url!r} names no host"
            raise ValueError(msg)
        _check_host(url, parts.hostname, parts.username)
        try:
            parts.port  # noqa: B018 - urllib checks the port when it is read
        except ValueError as exc:
            msg = f"{url!r} has an invalid port"
            raise ValueError(msg) from exc
    if not parts.path or not _PATH.fullmatch(parts.path) or parts.query or parts.fragment:
        msg = f"{url!r} isn't a repository URL: its path holds characters a repository path doesn't"
        raise ValueError(msg)
    return url


def _check_host(url: str, host: str, user: str | None) -> None:
    """Refuse a host or user name ssh could read as an option (`-oProxyCommand=...`).

    Raises:
        ValueError: If either starts with a dash or holds characters host and user names don't.
    """
    if not _HOST.fullmatch(host) or (user is not None and not _USER.fullmatch(user)):
        msg = f"{url!r} names a host or user that isn't one; host and user names start with a letter or digit"
        raise ValueError(msg)


class CommandFailed(Exception):  # noqa: N818 - says what happened, like ScriptExhausted
    """A `git` or `gh` command that exited with an error; carries what it said on standard error."""

    def __init__(self, command: Sequence[str], stderr: str) -> None:
        """Remember the command and its error output."""
        super().__init__(f"{' '.join(command)}: {stderr}")
        self.command = tuple(command)
        self.stderr = stderr


RunGitCommand = Callable[[Sequence[str], Path | None], str]
"""Runs a command (`git`, `gh`) in a directory and returns its standard output; the seam tests fake."""


@dataclass(frozen=True, slots=True)
class GitRunner:
    """Runs `git` and `gh` as argument lists, without credentials from `HZ_CREDENTIAL_*`, never waiting for a prompt."""

    timeout: float = TIMEOUT
    pinned: tuple[tuple[str, str], ...] = ()
    """Variables set for every command over what the environment has, e.g. a fixed commit identity."""

    def __call__(self, command: Sequence[str], cwd: Path | None) -> str:
        """Run `command` in `cwd`.

        Raises:
            HeinzelError: If the program isn't installed.
            CommandFailed: If it exits with an error or takes longer than the timeout.
        """
        executable = shutil.which(command[0])
        if executable is None:
            hint = "https://cli.github.com" if command[0] == "gh" else "https://git-scm.com"
            msg = f"`{command[0]}` isn't installed or not on PATH; install it ({hint}) and try again"
            raise HeinzelError(msg)
        # Its own session, so that a timeout stops the whole group: an ssh or credential helper git started too.
        process = subprocess.Popen(  # noqa: S603 - an argument list, executable resolved, URLs checked and after `--`
            [executable, *command[1:]],
            cwd=cwd,
            env=_environment() | dict(self.pinned),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            _stop(process)
            raise CommandFailed(command, f"took longer than {self.timeout:.0f} s") from exc
        if process.returncode != 0:
            raise CommandFailed(command, (stderr or stdout)[-MAX_STDERR:].strip())
        return stdout


def _stop(process: "subprocess.Popen[str]") -> None:
    """SIGTERM to the process group, then SIGKILL to whatever is still running after a grace period."""
    for signum in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signum)
        try:
            process.communicate(timeout=GRACE)
        except subprocess.TimeoutExpired:
            continue
        return


def _environment() -> dict[str, str]:
    """The environment for git and gh: an allowlist (ADR 0035), no hz credentials, no other repository, no prompts."""
    return {
        key: value
        for key, value in subprocess_environment().items()
        if (key in ALLOWED or key.startswith(ALLOWED_PREFIXES))
        and key not in DROPPED
        and not key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
    } | QUIET
