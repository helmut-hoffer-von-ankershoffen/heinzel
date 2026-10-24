"""Service of the system module."""

import platform
import re
import sys
import urllib.error
from collections import defaultdict
from typing import Any
from urllib.parse import urlsplit

from packaging.version import Version
from pydantic import BaseModel

from heinzel import InfoLet, Kernel, __version__
from heinzel.kernel import ReleaseCache
from heinzel.modules.system import _github
from heinzel.modules.system._models import ModuleInfo, ReleaseCheck, SystemInfo

DEFAULT_REPOSITORY = "helmut-hoffer-von-ankershoffen/heinzel"
"""Where releases come from when the installation doesn't say, e.g. in a development checkout."""
RELEASE_TIMEOUT = 5
"""Seconds to wait for the package source before giving up with a warning."""
_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")


def repository_of(requirement: str | None) -> str:
    """`owner/name` of the GitHub repository Heinzelmännchen was installed from; the default one otherwise."""
    if requirement is None or "git+" not in requirement:
        return DEFAULT_REPOSITORY
    parts = urlsplit(requirement.split("git+", 1)[1].strip())
    path = parts.path.split("@", 1)[0].strip("/").removesuffix(".git").split("/")
    if parts.hostname != "github.com" or len(path) != 2:  # noqa: PLR2004 - owner and name
        return DEFAULT_REPOSITORY
    if not all(_SEGMENT.fullmatch(segment) and segment not in {".", ".."} for segment in path):
        return DEFAULT_REPOSITORY
    return "/".join(path)


class SystemService:
    """Reports on the Heinzelmännchen installation."""

    def __init__(self, kernel: Kernel) -> None:
        """Create the service; the kernel is injected."""
        self._kernel = kernel

    def modules(self) -> list[ModuleInfo]:
        """Every registered module with the lets it advertises."""
        infos: list[ModuleInfo] = []
        for record in self._kernel.modules:
            lets: dict[str, list[str]] = defaultdict(list)
            for let in record.module.lets:
                lets[type(let).__name__].append(let.name)
            infos.append(
                ModuleInfo(
                    qualname=record.qualname,
                    summary=record.module.summary,
                    distribution=record.origin.distribution,
                    version=record.origin.version,
                    builtin=record.origin.builtin,
                    lets=dict(lets),
                )
            )
        return infos

    def info(self) -> SystemInfo:
        """Versions, interpreter, settings, modules and the sections modules contribute."""
        return SystemInfo(
            version=__version__,
            python_version=platform.python_version(),
            python_executable=sys.executable,
            platform=platform.platform(),
            settings=self._kernel.settings.model_dump(mode="json"),
            modules=self.modules(),
            plugin_errors=dict(self._kernel.errors),
            sections={record.qualname: self._section(let) for record, let in self._kernel.lets(InfoLet)},
            latest_release=ReleaseCache(self._kernel.settings.cache_dir).latest,
        )

    def release(self, requirement: str | None) -> ReleaseCheck:
        """Ask the package source for the latest release and cache it; offline or rate-limited is a warning.

        Never sends a token (ADR 0031): one unauthenticated GET with the `heinzel/<version>` User-Agent.

        Args:
            requirement: The requirement Heinzelmännchen was installed from, as in the uv tool receipt.
        """
        source = f"{_github.GITHUB_API}/repos/{repository_of(requirement)}/releases/latest"
        try:
            answer = _github.get_json(source, None, RELEASE_TIMEOUT)
            latest = str(Version(str(answer["tag_name"]).removeprefix("v")))
            html_url = str(answer["html_url"])
        # Isolation boundary: an unreachable, rate-limited or odd package source is a warning, never a crash.
        except (OSError, ValueError, KeyError, TypeError) as exc:  # InvalidVersion is a ValueError
            # An HTTPError reads best whole (it carries the status, e.g. 403 when rate-limited); others by reason.
            reason: object = exc
            if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError):
                reason = exc.reason
            warning = f"Could not ask {source} for the latest release ({reason})."
            return ReleaseCheck(
                current=__version__, latest=None, update_available=False, url=None, source=source, warnings=[warning]
            )
        ReleaseCache(self._kernel.settings.cache_dir).remember(latest)
        return ReleaseCheck(
            current=__version__,
            latest=latest,
            update_available=Version(latest) > Version(__version__),
            # Only a link to github.com is shown; anything else in the answer stays out of the terminal.
            url=html_url if html_url.startswith("https://github.com/") else None,
            source=source,
            warnings=[],
        )

    def _section(self, let: InfoLet) -> Any:  # noqa: ANN401 - sections are free-form JSON
        try:
            section = self._kernel.call(let.provider)
        except Exception as exc:  # noqa: BLE001 - a broken module must not break `hz system info`
            return {"error": f"{type(exc).__name__}: {exc}"}
        return section.model_dump(mode="json") if isinstance(section, BaseModel) else section
