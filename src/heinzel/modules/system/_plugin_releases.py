"""Newer stable releases of the plugins installed from PyPI, for automatic updates (ADR 0116).

Only a plugin whose receipt says it follows the package index can be asked cheaply: one GET of PyPI's JSON for its
project. A plugin from a git branch or a local folder is left out; `hz system update` still refreshes it when an update
runs for another reason.
"""

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, cast

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from heinzel import __version__
from heinzel.kernel import Installation, PluginReleases

PYPI = "https://pypi.org"
MAX_BYTES = 4 * 1024 * 1024

type Fetch = Callable[[str, float], Any]


def _fetch(url: str, timeout: float) -> Any:  # noqa: ANN401 - free-form JSON
    """GET `url` on PyPI with only a `heinzel/<version>` User-Agent; the seam tests fake.

    Raises:
        ValueError: If the URL is not on PyPI or the answer is too large or not JSON.
    """
    if not url.startswith(f"{PYPI}/pypi/"):
        msg = f"not a PyPI JSON URL: {url}"
        raise ValueError(msg)
    request = urllib.request.Request(url, headers={"User-Agent": f"heinzel/{__version__}"})  # noqa: S310 - https
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - https checked above
        body: bytes = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        msg = f"the answer is larger than {MAX_BYTES} bytes"
        raise ValueError(msg)
    return json.loads(body)


class PyPIPluginReleases(PluginReleases):
    """Asks PyPI for each plugin installed from the package index; stable releases only."""

    def __init__(self, installation: Installation, fetch: Fetch = _fetch) -> None:
        """Ask about the plugins in `installation`'s receipt, through `fetch`."""
        self._installation = installation
        self._fetch = fetch

    def newer(self, timeout: float) -> dict[str, str]:
        """Each index plugin whose latest stable PyPI release is newer than the installed one, mapped to it.

        Raises:
            HeinzelError: If the installed versions can't be read safely (from `Installation`).
        """
        names: dict[str, Requirement] = {}
        for pin in self._installation.plugin_pins():
            if pin.kind != "index":
                continue
            try:
                requirement = Requirement(pin.requirement)
            except InvalidRequirement:
                continue
            names[canonicalize_name(requirement.name)] = requirement
        if not names:
            return {}
        installed = self._installation.installed_versions(list(names))
        newer: dict[str, str] = {}
        for name, requirement in names.items():
            current = installed.get(name)
            latest = self._latest(name, timeout)
            if current is None or latest is None or not requirement.specifier.contains(latest):
                continue
            try:
                if Version(latest) > Version(current):
                    newer[name] = latest
            except InvalidVersion:
                continue
        return newer

    def _latest(self, name: str, timeout: float) -> str | None:
        data: object = self._fetch(f"{PYPI}/pypi/{urllib.parse.quote(name)}/json", timeout)
        info = cast("dict[str, object]", data).get("info") if isinstance(data, dict) else None
        version = cast("dict[str, object]", info).get("version") if isinstance(info, dict) else None
        if not isinstance(version, str):
            return None
        try:
            parsed = Version(version)
        except InvalidVersion:
            return None
        return None if parsed.is_prerelease or parsed.is_devrelease else str(parsed)


def plugin_releases(installation: Installation) -> PluginReleases:
    """The system module's `PluginReleases`: PyPI for plugins that follow the index."""
    return PyPIPluginReleases(installation)
