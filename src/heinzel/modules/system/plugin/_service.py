"""Service of the plugin module: plugins are extra requirements of Heinzelmännchen's uv tool environment (ADR 0006)."""

import contextlib
import json
import subprocess
import urllib.error
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast
from urllib.parse import urlencode, urlsplit

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from heinzel import HeinzelError, Kernel, __version__
from heinzel.kernel import CORE_DISTRIBUTION, Installation, subprocess_environment
from heinzel.modules.system._github import get_json
from heinzel.modules.system.plugin._models import AvailablePlugin, PluginInfo, PluginSearch, PluginSettings

TEMPLATE = "gh:helmut-hoffer-von-ankershoffen/heinzel"
REPOSITORY = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel"
FIRST_PARTY: Mapping[str, str] = MappingProxyType({"clock": "What time it is for everyone on the team."})
"""First-party plugins in `packages/heinzel-<name>/` (ADR 0017) by name, with their descriptions; found by
`hz system plugin add <name>`."""
TOPIC = "heinzel-plugin"
GITHUB_SEARCH = "https://api.github.com/search/repositories"
TIMEOUT = 10
"""Seconds to wait for GitHub before searching without it."""


def distributions_in_spec(spec: str) -> set[str]:
    """Best-effort guess of the distribution a spec installs, so adding a plugin again replaces the old copy."""
    names: set[str] = set()
    with contextlib.suppress(InvalidRequirement):
        names.add(canonicalize_name(Requirement(spec).name))
    path = urlsplit(spec.removeprefix("git+")).path.rstrip("/")
    if tail := path.rsplit("/", 1)[-1].split("@", 1)[0].removesuffix(".git"):
        names.add(canonicalize_name(tail))
    return names


class PluginService:
    """Lists, adds, removes and scaffolds plugins."""

    def __init__(self, kernel: Kernel, installation: Installation, settings: PluginSettings) -> None:
        """Create the service; the kernel, the installation and the settings are injected."""
        self._kernel = kernel
        self._installation = installation
        self._settings = settings

    def installed(self) -> list[PluginInfo]:
        """Plugins loaded by this process."""
        plugins: dict[str, PluginInfo] = {}
        for record in self._kernel.modules:
            if record.origin.builtin:
                continue
            plugin = plugins.setdefault(
                record.origin.distribution,
                PluginInfo(distribution=record.origin.distribution, version=record.origin.version, modules=[]),
            )
            plugin.modules.append(record.qualname)
        return list(plugins.values())

    def first_party(self, name: str) -> str | None:
        """The spec of a first-party plugin, from the same source and version as the installed core; None otherwise.

        A core installed from git gets the plugin from the same repository and revision; a development checkout gets
        it from its `packages/` directory; anything else gets it from the release tag of the running version.
        """
        if name not in FIRST_PARTY:
            return None
        subdirectory = f"packages/heinzel-{name}"
        checkout = Path(__file__).resolve().parents[5] / subdirectory
        if checkout.is_dir():
            return f"heinzel-{name} @ {checkout.as_uri()}"
        core = (self._installation.requirements() or [""])[0]
        if " @ git+" in core:
            return f"heinzel-{name} @ {core.split(' @ ', 1)[1].split('#', 1)[0]}#subdirectory={subdirectory}"
        return f"heinzel-{name} @ git+{REPOSITORY}@v{__version__}#subdirectory={subdirectory}"

    def add_command(self, spec: str) -> list[str]:
        """The command that installs the plugin `spec`: a first-party name like `clock`, a git URL or a path."""
        spec = self.first_party(spec) or spec
        requirements = self._installation.requirements()
        if requirements is None:
            return self._installation.pip_command("install", spec)
        core, *plugins = requirements
        replaced = distributions_in_spec(spec)
        kept = [r for r in plugins if canonicalize_name(Requirement(r).name) not in replaced]
        return self._installation.tool_install_command(core, [*kept, spec])

    def remove_command(self, distribution: str) -> list[str]:
        """The command that uninstalls the plugin `distribution`.

        Raises:
            HeinzelError: If asked to remove Heinzelmännchen itself, or a plugin that isn't installed with
            Heinzelmännchen.
        """
        target = canonicalize_name(distribution)
        if target == CORE_DISTRIBUTION:
            msg = "Refusing to remove Heinzelmännchen itself. Use `uv tool uninstall heinzel`."
            raise HeinzelError(msg)
        plugins_installed = {canonicalize_name(p.distribution) for p in self.installed()}
        if target not in plugins_installed:
            known = ", ".join(sorted(plugins_installed)) or "none"
            msg = f"{distribution!r} is not an installed plugin. Installed plugins: {known}."
            raise HeinzelError(msg)
        requirements = self._installation.requirements()
        if requirements is None:
            return self._installation.pip_command("uninstall", distribution)
        core, *plugins = requirements
        kept = [r for r in plugins if canonicalize_name(Requirement(r).name) != target]
        if len(kept) == len(plugins):
            msg = f"No plugin distribution named {distribution!r} is installed with Heinzelmännchen."
            raise HeinzelError(msg)
        return self._installation.tool_install_command(core, kept)

    def add(self, spec: str) -> list[PluginInfo]:
        """Install a plugin and return the plugins installed afterwards."""
        self._installation.run(self.add_command(spec), f"Failed to add plugin {spec!r}.", f"Adding plugin {spec}")
        return self._installed_now()

    def remove(self, distribution: str) -> list[PluginInfo]:
        """Uninstall a plugin and return the plugins installed afterwards."""
        self._installation.run(
            self.remove_command(distribution), f"Failed to remove plugin {distribution!r}.", f"Removing {distribution}"
        )
        return self._installed_now()

    def new(
        self, destination: Path, template: str = TEMPLATE, ref: str | None = None, data: Sequence[str] = ()
    ) -> Path:
        """Scaffold a plugin repository from the Heinzelmännchen copier template.

        Copier asks for every answer missing from `data` (`key=value`), so with all answers given it runs unattended.
        The template is pinned to this Heinzelmännchen's release tag unless `ref` says otherwise.

        Raises:
            HeinzelError: If copier fails.
        """
        ref = ref or (f"v{__version__}" if __version__ != "0.0.0" else "HEAD")
        command = [self._installation.uv, "tool", "run", "copier", "copy", "--vcs-ref", ref]
        for answer in data:
            command += ["--data", answer]
        command += [template, str(destination)]
        # Copier asks questions, so it gets the terminal instead of a spinner.
        interactive = subprocess.run(command, check=False, env=subprocess_environment())  # noqa: S603 - trusted parts, no shell; interactive: no timeout
        if interactive.returncode != 0:
            msg = f"Failed to scaffold a plugin into {destination}."
            raise HeinzelError(msg)
        return destination

    def search(self, text: str = "") -> PluginSearch:
        """Plugins one can install: first-party ones, then GitHub repositories with the topic heinzel-plugin.

        PyPI is not searched: it has no keyword search API (XML-RPC search is disabled), its search page sits behind
        a JavaScript challenge, and its simple index is tens of megabytes.
        """
        installed = {canonicalize_name(p.distribution) for p in self.installed()}
        words = text.casefold().split()
        found = [
            AvailablePlugin(
                name=f"heinzel-{name}",
                short_name=name,
                description=description,
                source="first-party",
                community=False,
                install_spec=name,
                url=f"{REPOSITORY}/tree/main/packages/heinzel-{name}",
                stars=None,
                installed=f"heinzel-{name}" in installed,
            )
            for name, description in sorted(FIRST_PARTY.items())
            if all(word in f"heinzel-{name} {description}".casefold() for word in words)
        ]
        warnings: list[str] = []
        try:
            repositories = self._github_repositories(text)
        # Isolation boundary: GitHub being unreachable, rate-limited or odd must not take the first-party results down.
        except (OSError, ValueError, KeyError, TypeError) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            warnings.append(f"GitHub could not be searched ({reason}); showing first-party plugins only.")
            repositories = []
        known = {plugin.name for plugin in found}
        community = [
            AvailablePlugin(
                name=repository["name"],
                short_name=repository["name"].removeprefix("heinzel-"),
                description=repository.get("description") or "",
                source="github",
                community=True,
                install_spec=f"git+{repository['html_url']}",
                url=repository["html_url"],
                stars=repository.get("stargazers_count") or 0,
                installed=canonicalize_name(repository["name"]) in installed,
            )
            for repository in repositories
            if canonicalize_name(repository["name"]) not in known and not repository.get("archived")
        ]
        community.sort(key=lambda plugin: (-(plugin.stars or 0), plugin.name))
        return PluginSearch(plugins=[*found, *community], warnings=warnings)

    def _github_repositories(self, text: str) -> list[dict[str, Any]]:
        """Repositories with the topic heinzel-plugin that match `text`.

        Raises:
            OSError: If GitHub cannot be reached or answers with an error, e.g. when rate-limited.
            ValueError: If the answer is not JSON.
            KeyError: If the answer has no list of repositories.
            TypeError: If the answer has an unexpected shape.
        """
        query = urlencode({"q": f"topic:{TOPIC} {text}".strip(), "sort": "stars", "per_page": 100})
        items = get_json(f"{GITHUB_SEARCH}?{query}", self._settings.github_token, TIMEOUT)["items"]
        if not isinstance(items, list):
            msg = f"expected a list of repositories, got {type(items).__name__}"
            raise TypeError(msg)
        # Free-form JSON: repositories are dicts; their fields are read defensively in search().
        return [cast("dict[str, Any]", item) for item in cast("list[object]", items) if isinstance(item, dict)]

    def _installed_now(self) -> list[PluginInfo]:
        return [
            PluginInfo.model_validate(item)
            for item in json.loads(self._installation.hz("system", "plugin", "list", "--json"))
        ]
