"""Repository URLs a person may join, as the team module runs `git` and `gh` on them.

The checks and the hardened runner are the kernel's (`heinzel.kernel.GitRunner`, `check_repository_url`), shared
with the state module that pushes to your state repository; the names here are the team module's own for them, plus
what only the team module needs: which GitHub repository a URL names, and what its checkout is called.
"""

import re
from urllib.parse import urlsplit

from heinzel.kernel import GIT_HARDENING as HARDENING
from heinzel.kernel import CommandFailed
from heinzel.kernel import GitRunner as Runner
from heinzel.kernel import RunCommand as Run
from heinzel.kernel import check_repository_url as check_url
from heinzel.kernel import git_command as git

_SCP = re.compile(r"^(?P<user>[A-Za-z0-9._-]+)@(?P<host>[A-Za-z0-9.-]+):(?P<path>[A-Za-z0-9._~/-]+)$")
_GITHUB_PATH = re.compile(
    r"^/?(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/(?P<name>[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"
)
GITHUB_REPOSITORY = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_-][A-Za-z0-9._-]{0,99}$")
"""`owner/name` as GitHub allows it, never starting with a dash or a dot."""


def github_repository(url: str) -> str | None:
    """`owner/name` when `url` is a github.com repository, which `gh` can open pull requests on; None otherwise."""
    if (match := _SCP.fullmatch(url)) is not None:
        host, path = match["host"], match["path"]
    elif "://" in url:
        parts = urlsplit(url)
        host, path = parts.hostname or "", parts.path
    else:
        return None
    if host.lower() != "github.com" or (found := _GITHUB_PATH.fullmatch(path)) is None:
        return None
    return f"{found['owner']}/{found['name']}"


def directory_name(url: str) -> str:
    """The name a checkout of `url` gets: the repository's last path part without `.git`, as safe characters."""
    last = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1].removesuffix(".git")
    cleaned = re.sub(r"[^a-z0-9]+", "-", last.lower()).strip("-")
    return cleaned or "team"


__all__ = [
    "GITHUB_REPOSITORY",
    "HARDENING",
    "CommandFailed",
    "Run",
    "Runner",
    "check_url",
    "directory_name",
    "git",
    "github_repository",
]
