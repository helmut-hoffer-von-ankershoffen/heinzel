"""Service of the plugin module: plugins are extra requirements of Heinzelmännchen's uv tool environment (ADR 0006).

Adding one runs its code with your rights, so `add` first shows what it would install (source, version, publisher,
pinned to the commit or release it read) and installs only with `confirm`; every change is recorded in
`<data_dir>/plugins.jsonl` (ADR 0047).
"""

import base64
import contextlib
import json
import shlex
import subprocess
import threading
import tomllib
import urllib.error
from collections.abc import Collection, Generator, Mapping, Sequence
from datetime import UTC, datetime
from importlib.metadata import Distribution, entry_points
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast
from urllib.parse import urlencode, urlsplit

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from heinzel import HeinzelError, Kernel, __version__
from heinzel.kernel import (
    CORE_DISTRIBUTION,
    ENTRY_POINT_GROUP,
    KERNEL_SECTION,
    ConfigEditor,
    Installation,
    LayerLet,
    ModuleRecord,
    OperationLet,
    PlaybookLet,
    ProviderLet,
    ThemeLet,
    subprocess_environment,
)
from heinzel.modules.system._github import get_json
from heinzel.modules.system.plugin._models import (
    Action,
    AvailablePlugin,
    PluginCandidate,
    PluginChange,
    PluginEvent,
    PluginInfo,
    PluginKind,
    PluginLayer,
    PluginSearch,
    PluginSettings,
    Source,
)
from heinzel.modules.system.plugin._sources import (
    PREFIX,
    JsonGetter,
    Metadata,
    PluginSource,
    browser_url,
    from_github,
    from_pypi,
    git_credentials,
    github_getter,
    installed_repository_url,
    parse,
    pypi_get,
    repository_url,
)

TEMPLATE = "gh:helmut-hoffer-von-ankershoffen/heinzel"
REPOSITORY = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel"
MAINTAINERS = "Heinzelmännchen maintainers"
FIRST_PARTY: Mapping[str, str] = MappingProxyType(
    {
        "atlassian": "Read Jira issues and Confluence pages through acli: the tracker and documents capabilities.",
        "clock": "What time it is for everyone on the team.",
        "github": "Read pull requests and issues from GitHub: the code-host and tracker capabilities.",
        "google": "Read Google Docs, Sheets, Drive, Calendar and Gmail: the documents, calendar and mail capabilities.",
        "zoom": "Read meetings from Zoom's meeting-summary emails: the meetings capability.",
    }
)
"""First-party plugins in `plugins/heinzel-<name>/` (ADR 0017) by name, with their descriptions; found by
`hz system plugin add <name>`. Released with the core, never installed by default."""
REFERENCE: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "acme": (
            (
                "Acme, a made-up company: the reference layers (company, org, team, site), adapters bound by "
                "configuration, a weekly-report playbook and onboarding end to end (ADR 0040)."
            ),
            "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-acme",
        ),
    }
)
"""First-party plugins in repositories of their own, by name: their descriptions and repositories."""
TOPIC = "heinzel-plugin"
GITHUB_SEARCH = "https://api.github.com/search/repositories"
GITHUB_REPOS = "https://api.github.com/repos"
MANIFEST = "heinzel-plugins.toml"
TIMEOUT = 10
"""Seconds to wait for GitHub before searching without it."""
HISTORY = "plugins.jsonl"


def distributions_in_spec(spec: str) -> set[str]:
    """Best-effort guess of the distribution a spec installs, so adding a plugin again replaces the old copy."""
    names: set[str] = set()
    with contextlib.suppress(InvalidRequirement):
        names.add(canonicalize_name(Requirement(spec).name))
    parts = urlsplit(spec.split(" @ ", 1)[-1].removeprefix("git+"))
    key, _, subdirectory = parts.fragment.partition("=")
    if key == "subdirectory" and subdirectory.strip("/"):
        # A monorepo: the plugin is named after its directory, not after the repository or the ref.
        tail = subdirectory.rstrip("/").rsplit("/", 1)[-1]
    else:
        # The ref after `@` may hold slashes (`@feat/x`), so the repository is what comes before it.
        tail = parts.path.split("@", 1)[0].rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    if tail:
        names.add(canonicalize_name(tail))
    return names


TEMPLATE_OPERATIONS = frozenset({"info"})
"""Operations every plugin scaffolded from the template has (`hz <name> info`): no feature of its own."""


def plugin_kinds(*, capabilities: bool, themes: bool, features: bool, playbooks: bool) -> list[PluginKind]:
    """What a plugin is, in the order `PluginInfo.kinds` documents: connector, theme, capability, playbook."""
    flags: dict[PluginKind, bool] = {
        "connector": capabilities,
        "theme": themes,
        "capability": features,
        "playbook": playbooks,
    }
    return [kind for kind, has in flags.items() if has]


def short_name(distribution: str) -> str:
    """`heinzel-clock` → `clock`."""
    return canonicalize_name(distribution).removeprefix(PREFIX)


class PluginService:
    """Lists, adds, removes, enables, disables and scaffolds plugins."""

    def __init__(self, kernel: Kernel, installation: Installation, settings: PluginSettings) -> None:
        """Create the service; the kernel, the installation and the settings are injected."""
        self._kernel = kernel
        self._installation = installation
        self._settings = settings
        self._changing = threading.Lock()
        self.github_api: JsonGetter = github_getter(settings.github_token)
        """Reads the GitHub API for `add`; tests replace it."""
        self.pypi: JsonGetter = pypi_get
        """Reads PyPI's JSON API for `add`; tests replace it."""
        self.distributions: Mapping[str, Distribution] | None = None
        """Installed plugin distributions by canonical name; read from the entry points when None (tests set it)."""

    # Listing.

    def installed(self) -> list[PluginInfo]:
        """Every installed plugin, enabled or not, with where it came from and what it brings."""
        records: dict[str, list[ModuleRecord]] = {}
        enabled: dict[str, bool] = {}
        for record in self._kernel.modules:
            if not record.origin.builtin:
                records.setdefault(canonicalize_name(record.origin.distribution), []).append(record)
                enabled[canonicalize_name(record.origin.distribution)] = True
        for record in self._kernel.blocked:
            if not record.origin.builtin:
                name: str = canonicalize_name(record.origin.distribution)
                records.setdefault(name, []).append(record)
                enabled.setdefault(name, False)
        distributions = self._plugin_distributions()
        errors = self._errors_by_distribution()
        plugins: list[PluginInfo] = []
        # What the kernel loaded, blocked or failed to load; with `[hz] plugins_enabled` off, that is nothing.
        for name in sorted(set(records) | set(errors)):
            mine = sorted(records.get(name, []), key=lambda r: r.path)
            dist = distributions.get(name)
            origin = mine[0].origin if mine else None
            homepage = origin.url if origin else None
            kind, url, revision = _source_of(dist, homepage)
            lets = [let for record in mine for let in record.module.lets]
            capabilities = sorted({f"{let.adapter}:{let.capability}" for let in lets if isinstance(let, ProviderLet)})
            themes = sorted({let.name for let in lets if isinstance(let, ThemeLet)})
            playbooks = [r.qualname for r in mine if any(isinstance(let, PlaybookLet) for let in r.module.lets)]
            features = any(isinstance(let, OperationLet) and let.name not in TEMPLATE_OPERATIONS for let in lets)
            plugins.append(
                PluginInfo(
                    distribution=origin.distribution if origin else (dist.name if dist else name),
                    # The metadata installed now: the kernel read its origin at start, before an upgrade replaced it.
                    version=dist.version if dist else (origin.version if origin else "unknown"),
                    modules=[r.qualname for r in mine],
                    name=short_name(name),
                    summary=str(dist.metadata.get("Summary") or "") if dist else "",
                    source_kind=kind,
                    source_url=url,
                    revision=revision,
                    homepage=homepage,
                    first_party=_is_first_party(name, kind, url),
                    enabled=enabled.get(name, False) and self._kernel.settings.plugins_enabled,
                    error=errors.get(name),
                    layers=[
                        PluginLayer(module=let.name, parent=let.parent, site=let.site)
                        for record in mine
                        for let in record.module.lets
                        if isinstance(let, LayerLet)
                    ],
                    capabilities=capabilities,
                    playbooks=playbooks,
                    themes=themes,
                    kinds=plugin_kinds(
                        capabilities=bool(capabilities),
                        themes=bool(themes),
                        features=features,
                        playbooks=bool(playbooks),
                    ),
                )
            )
        return plugins

    def history(self) -> list[PluginEvent]:
        """Plugin changes carried out on this machine, oldest first."""
        path = self._history_path()
        if not path.is_file():
            return []
        events: list[PluginEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            with contextlib.suppress(ValueError):  # a torn or hand-edited line costs that line, not the history
                events.append(PluginEvent.model_validate_json(line))
        return events

    # Adding.

    def first_party(self, name: str) -> str | None:
        """The spec of a first-party plugin, from the same source and version as the installed core; None otherwise.

        A core installed from git gets the plugin from the same repository and revision; a development checkout gets
        it from its `plugins/` directory; anything else gets it from the release tag of the running version. The
        distribution name works too (`heinzel-github`), since `hz system plugin remove` takes that one. Plugins in a
        repository of their own (`acme`) come from that repository's default branch.
        """
        name = name.strip().removeprefix(PREFIX)
        if name in REFERENCE:
            return f"{PREFIX}{name} @ git+{REFERENCE[name][1]}"
        if name not in FIRST_PARTY:
            return None
        subdirectory = f"plugins/heinzel-{name}"
        checkout = Path(__file__).resolve().parents[5] / subdirectory
        if checkout.is_dir():
            return f"heinzel-{name} @ {checkout.as_uri()}"
        core = (self._installation.requirements() or [""])[0]
        if " @ git+" in core:
            return f"heinzel-{name} @ {core.split(' @ ', 1)[1].split('#', 1)[0]}#subdirectory={subdirectory}"
        return f"heinzel-{name} @ git+{REPOSITORY}@v{__version__}#subdirectory={subdirectory}"

    def candidate(self, spec: str) -> PluginCandidate:
        """What `add` would install for `spec`, read from PyPI or GitHub and pinned where it can be.

        Raises:
            HeinzelError: If the source isn't one `add` takes, or names a package, repository or ref that doesn't exist.
        """
        resolved = self.first_party(spec)
        name = spec.strip().removeprefix(PREFIX)
        if resolved is not None and name in FIRST_PARTY:
            return self._first_party_candidate(spec, name, resolved)
        source = parse(resolved or spec)
        warnings: list[str] = []
        metadata = Metadata()
        try:
            if source.kind == "pypi":
                metadata = from_pypi(source, self.pypi)
            elif source.github is not None:
                metadata = from_github(source, self.github_api)
            else:
                warnings.append(
                    f"{urlsplit(source.url or '').hostname} isn't GitHub: its name, version and publisher can't be "
                    "read before installing; add it only if you trust that repository."
                )
        # Isolation boundary: an unreachable PyPI or GitHub leaves the source unverified, which the warning says.
        except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            warnings.append(f"What it is couldn't be read ({reason}): only the source below is known.")
        distribution = metadata.name or source.name
        page: str | None
        if source.kind == "pypi" and source.requirement is not None:
            pinned = f"{distribution}=={metadata.version}" if metadata.version else str(source.requirement)
            requirement, page = pinned, metadata.page or f"https://pypi.org/project/{distribution}/"
        else:
            requirement, page = source.git_requirement(distribution, metadata.revision), metadata.page or source.url
        self._check(spec, source, metadata, requirement, first_party=resolved is not None)
        if distribution is not None and not distribution.startswith(PREFIX):
            warnings.append(f"It is named {distribution}, not {PREFIX}<name> as plugins are (ADR 0025).")
        installed = self._installed_version(distribution)
        return PluginCandidate(
            spec=spec,
            requirement=requirement,
            distribution=distribution,
            version=metadata.version,
            summary=metadata.summary,
            source=source.kind,
            url=page or "",
            publisher=MAINTAINERS if resolved else metadata.publisher,
            first_party=resolved is not None,
            private=metadata.private,
            revision=metadata.revision,
            installed_version=installed,
            warnings=warnings,
        )

    def add(self, spec: str, *, confirm: bool = False) -> PluginChange:
        """Show what `spec` installs; with `confirm`, install exactly that, record it and list the plugins after.

        Raises:
            HeinzelError: If the source is refused, unknown, or installing fails.
        """
        candidate = self.candidate(spec)
        if not confirm:
            return PluginChange(
                action="add",
                done=False,
                distribution=candidate.distribution,
                candidate=candidate,
                plugins=self.installed(),
                restart_required=False,
                next=[f"hz system plugin add {shlex.quote(candidate.requirement)} --confirm"],
            )
        with self._exclusive():
            self._installation.run(
                self.add_command(candidate.requirement),
                f"Failed to add plugin {spec!r}.",
                f"Adding plugin {candidate.distribution or spec}",
                self._git_environment(),
            )
            self._record(
                "add",
                candidate.distribution,
                requirement=candidate.requirement,
                source=candidate.source,
                version=candidate.version,
                publisher=candidate.publisher,
            )
            return self._changed("add", candidate.distribution, candidate)

    def develop(self, path: Path) -> PluginChange:
        """Install the plugin in a directory on this machine, as `add` would a released one.

        Raises:
            HeinzelError: If `path` is no directory with a pyproject.toml, or installing fails.
        """
        directory = path.expanduser().resolve()
        pyproject = directory / "pyproject.toml"
        if not pyproject.is_file():
            msg = f"{directory} has no pyproject.toml; scaffold a plugin there with `hz system plugin new {path}`."
            raise HeinzelError(msg)
        try:
            project = cast("dict[str, Any]", tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"])
            name, version = canonicalize_name(str(project["name"])), project.get("version")
        except (tomllib.TOMLDecodeError, KeyError) as exc:
            msg = f"{pyproject} names no project ({exc}); is it a plugin's checkout?"
            raise HeinzelError(msg) from exc
        with self._exclusive():
            self._installation.run(
                self.add_command(f"{name} @ {directory.as_uri()}"),
                f"Failed to add plugin {path}.",
                f"Adding plugin {name}",
            )
            self._record("develop", name, requirement=str(directory), source="path", version=version and str(version))
            return self._changed("develop", name, None)

    def add_command(self, spec: str) -> list[str]:
        """The command that installs the plugin `spec`: a requirement, a git URL or a path, as given."""
        requirements = self._installation.requirements()
        if requirements is None:
            return self._installation.pip_command("install", spec)
        core, *plugins = requirements
        replaced = distributions_in_spec(spec)
        kept = [r for r in plugins if canonicalize_name(Requirement(r).name) not in replaced]
        return self._installation.tool_install_command(core, [*kept, spec])

    # Removing, enabling, disabling.

    def remove_command(self, distribution: str) -> list[str]:
        """The command that uninstalls the plugin `distribution`.

        Raises:
            HeinzelError: If asked to remove Heinzelmännchen itself, or a plugin that isn't installed with
            Heinzelmännchen.
        """
        target = self._known(distribution)
        requirements = self._installation.requirements()
        if requirements is None:
            return self._installation.pip_command("uninstall", distribution)
        core, *plugins = requirements
        kept = [r for r in plugins if canonicalize_name(Requirement(r).name) != target]
        if len(kept) == len(plugins):
            msg = f"No plugin distribution named {distribution!r} is installed with Heinzelmännchen."
            raise HeinzelError(msg)
        return self._installation.tool_install_command(core, kept)

    def remove(self, distribution: str) -> PluginChange:
        """Uninstall a plugin and return the plugins installed afterwards.

        Raises:
            HeinzelError: If it isn't an installed plugin, or uninstalling fails.
        """
        with self._exclusive():
            version = next((p.version for p in self.installed() if _same(p.distribution, distribution)), None)
            self._installation.run(
                self.remove_command(distribution),
                f"Failed to remove plugin {distribution!r}.",
                f"Removing {distribution}",
                self._git_environment(),  # reinstalling fetches the other git plugins again, private ones too
            )
            self._record("remove", canonicalize_name(distribution), version=version)
            return self._changed("remove", canonicalize_name(distribution), None)

    def enable(self, distribution: str) -> PluginChange:
        """Switch a disabled plugin's modules on again in your user configuration.

        Raises:
            HeinzelError: If it isn't an installed plugin, or another file or variable keeps it disabled.
        """
        return self._switch(distribution, on=True)

    def disable(self, distribution: str) -> PluginChange:
        """Switch a plugin's modules off in your user configuration (`[hz] disabled_modules`), keeping it installed.

        Raises:
            HeinzelError: If it isn't an installed plugin, or its modules can't be told because it fails to load.
        """
        return self._switch(distribution, on=False)

    def _switch(self, distribution: str, *, on: bool) -> PluginChange:
        target = self._known(distribution)
        plugin = next(p for p in self.installed() if canonicalize_name(p.distribution) == target)
        tops = sorted({m for m in plugin.modules if "." not in m})
        if not tops:
            msg = f"{distribution} brings no modules Heinzelmännchen could load ({plugin.error or 'none found'})."
            raise HeinzelError(msg)
        action: Action = "enable" if on else "disable"
        with self._exclusive():
            current = set(self._user_disabled())
            if on and (elsewhere := set(tops) & (set(self._kernel.settings.disabled_modules) - current)):
                msg = (
                    f"{', '.join(sorted(elsewhere))} stay disabled by a project hz.toml, the site file or "
                    "HZ_DISABLED_MODULES, which `hz system config show` names; your user file doesn't disable them."
                )
                raise HeinzelError(msg)
            wanted = sorted(current - set(tops)) if on else sorted(current | set(tops))
            editor = self._kernel.resolve(ConfigEditor)
            key = f"{KERNEL_SECTION}.disabled_modules"
            if wanted != sorted(current):
                if wanted:
                    editor.write(key, wanted, "user")
                else:
                    editor.unset(key, "user")
            self._record(action, target, version=plugin.version)
        change = self._changed(action, target, None)
        if not on:
            change.next = [f"hz system plugin enable {target}"]
        return change

    # Searching and scaffolding.

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
        listed = {name: (description, f"{REPOSITORY}/tree/main/plugins/heinzel-{name}")
                  for name, description in FIRST_PARTY.items()} | dict(REFERENCE)  # fmt: skip
        found = [
            AvailablePlugin(
                name=f"heinzel-{name}",
                short_name=name,
                description=description,
                source="first-party",
                community=False,
                install_spec=name,
                url=url,
                repository_url=url,
                stars=None,
                installed=f"heinzel-{name}" in installed,
            )
            for name, (description, url) in sorted(listed.items())
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
            plugin
            for repository in repositories
            if canonicalize_name(repository["name"]) not in known and not repository.get("archived")
            for plugin in self._community_plugins(repository, installed)
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

    def _community_plugins(self, repository: dict[str, Any], installed: Collection[str]) -> list[AvailablePlugin]:
        """The plugins of one repository with the topic: one per entry of a monorepo, else the repository itself."""
        url = repository["html_url"]
        stars = repository.get("stargazers_count") or 0
        entries = self._monorepo_plugins(repository)
        if not entries:
            return [
                AvailablePlugin(
                    name=repository["name"],
                    short_name=repository["name"].removeprefix("heinzel-"),
                    description=repository.get("description") or "",
                    source="github",
                    community=True,
                    install_spec=f"git+{url}",
                    url=url,
                    repository_url=repository_url(f"git+{url}"),
                    stars=stars,
                    installed=canonicalize_name(repository["name"]) in installed,
                )
            ]
        branch = repository.get("default_branch") or "HEAD"
        return [
            AvailablePlugin(
                name=name,
                short_name=name.removeprefix("heinzel-"),
                description=summary,
                source="github",
                community=True,
                install_spec=f"{name} @ git+{url}#subdirectory={path}",
                url=f"{url}/tree/{branch}/{path}",
                repository_url=repository_url(f"git+{url}"),
                stars=stars,
                installed=canonicalize_name(name) in installed,
            )
            for name, path, summary in entries
        ]

    def _monorepo_plugins(self, repository: dict[str, Any]) -> list[tuple[str, str, str]]:
        """(distribution, path, summary) of each plugin a repository declares; empty for a single-plugin repository.

        A repository declares its plugins in `heinzel-plugins.toml` (`[[plugin]]` with `name`, `path`, `summary`) or,
        failing that, by `plugins/*/pyproject.toml` files whose distribution name starts with `heinzel-`. Anything
        GitHub answers that does not fit means: a single-plugin repository.
        """
        full_name = repository.get("full_name")
        if not full_name:
            return []
        api = f"{GITHUB_REPOS}/{full_name}/contents"
        manifest = self._file(f"{api}/{MANIFEST}")
        if manifest is not None:
            try:
                declared = tomllib.loads(manifest).get("plugin", [])
                return [
                    (
                        name if (name := str(entry["name"])).startswith("heinzel-") else f"heinzel-{name}",
                        str(entry.get("path") or f"plugins/heinzel-{str(entry['name']).removeprefix('heinzel-')}"),
                        str(entry.get("summary") or ""),
                    )
                    for entry in declared
                ]
            except (tomllib.TOMLDecodeError, KeyError, TypeError, AttributeError):
                return []
        try:
            listing = get_json(f"{api}/plugins", self._settings.github_token, TIMEOUT)
        except (OSError, ValueError):
            return []
        if not isinstance(listing, list):
            return []
        found: list[tuple[str, str, str]] = []
        for item in sorted(
            (i for i in listing if isinstance(i, dict) and i.get("type") == "dir"), key=lambda i: str(i.get("name"))
        ):
            path = f"plugins/{item.get('name')}"
            text = self._file(f"{api}/{path}/pyproject.toml")
            try:
                project = tomllib.loads(text).get("project", {}) if text is not None else {}
            except tomllib.TOMLDecodeError:
                continue
            dist = project.get("name")
            if isinstance(dist, str) and dist.startswith("heinzel-"):
                found.append((dist, path, str(project.get("description") or "")))
        return found

    def _file(self, url: str) -> str | None:
        """The text of a file from GitHub's contents API; None if it is not there or not readable."""
        try:
            answer = get_json(url, self._settings.github_token, TIMEOUT)
            if isinstance(answer, dict) and answer.get("encoding") == "base64":
                return base64.b64decode(str(answer["content"])).decode()
        except (OSError, ValueError, KeyError):
            return None
        return None

    # Helpers.

    @staticmethod
    def _check(spec: str, source: PluginSource, metadata: Metadata, requirement: str, *, first_party: bool) -> None:
        """Refuse what would install something other than what is shown, or stand in for a first-party plugin.

        Raises:
            HeinzelError: If the plugin's own name differs from the one asked for, the requirement carries extras
                or markers, or a plugin from elsewhere is named like a first-party one.
        """
        if source.name and metadata.name and canonicalize_name(source.name) != metadata.name:
            msg = f"{spec!r} asks for {source.name}, but the repository's pyproject.toml names {metadata.name}."
            raise HeinzelError(msg)
        # An unnamed git URL (a host whose metadata can't be read) is no PEP 508 requirement; uv names it on install.
        parsed = Requirement(requirement) if source.kind == "pypi" or " @ " in requirement else None
        if parsed is not None and (parsed.extras or parsed.marker is not None):
            msg = f"Add {parsed.name} without extras or environment markers; a plugin brings what it needs."
            raise HeinzelError(msg)
        reserved = {f"{PREFIX}{name}" for name in (*FIRST_PARTY, *REFERENCE)}
        if not first_party and (taken := sorted(distributions_in_spec(requirement) & reserved)):
            short = taken[0].removeprefix(PREFIX)
            msg = (
                f"{spec!r} would install {taken[0]}, the name of a first-party plugin; add that one by name "
                f"(`hz system plugin add {short}`), or a plugin with a name of its own."
            )
            raise HeinzelError(msg)

    def _first_party_candidate(self, spec: str, name: str, requirement: str) -> PluginCandidate:
        """A plugin released with the core: its version is the core's, its publisher the maintainers."""
        local = " @ file:" in requirement
        return PluginCandidate(
            spec=spec,
            requirement=requirement,
            distribution=f"{PREFIX}{name}",
            version=__version__,
            summary=FIRST_PARTY[name],
            source="path" if local else "git",
            url=f"{REPOSITORY}/tree/main/plugins/heinzel-{name}",
            publisher=MAINTAINERS,
            first_party=True,
            private=False,
            revision=None,
            installed_version=self._installed_version(f"{PREFIX}{name}"),
            warnings=["From this checkout's plugins/ directory."] if local else [],
        )

    def _known(self, distribution: str) -> str:
        """The canonical name of an installed plugin.

        Raises:
            HeinzelError: If it names Heinzelmännchen itself or no installed plugin.
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
        return target

    def _installed_version(self, distribution: str | None) -> str | None:
        if distribution is None:
            return None
        return next((p.version for p in self.installed() if _same(p.distribution, distribution)), None)

    def _plugin_distributions(self) -> Mapping[str, Distribution]:
        if self.distributions is not None:
            return self.distributions
        found: dict[str, Distribution] = {}
        for entry_point in entry_points(group=ENTRY_POINT_GROUP):
            if entry_point.dist is not None and canonicalize_name(entry_point.dist.name) != CORE_DISTRIBUTION:
                found[canonicalize_name(entry_point.dist.name)] = entry_point.dist
        return found

    def _errors_by_distribution(self) -> dict[str, str]:
        """Load errors of plugins (the kernel keys them by entry point name) by distribution."""
        errors: dict[str, str] = {}
        by_entry_point = {
            ep.name: canonicalize_name(ep.dist.name) for ep in entry_points(group=ENTRY_POINT_GROUP) if ep.dist
        }
        for name, problem in self._kernel.errors.items():
            errors[by_entry_point.get(name, canonicalize_name(name))] = problem
        return errors

    def _user_disabled(self) -> list[str]:
        """`[hz] disabled_modules` as your user file sets it, the one file `enable` and `disable` write."""
        for file, content in self._kernel.config.layers:
            if file.scope == "user":
                table = cast("dict[str, Any]", content.get(KERNEL_SECTION) or {})
                value = table.get("disabled_modules", table.get("disabled-modules", []))
                return [str(v) for v in cast("list[object]", value)] if isinstance(value, list) else []
        return []

    def _git_environment(self) -> dict[str, str]:
        return git_credentials(subprocess_environment())

    @contextlib.contextmanager
    def _exclusive(self) -> Generator[None]:
        """One change at a time: two uv reinstalls of the same environment would race.

        Raises:
            HeinzelError: If another change is running.
        """
        if not self._changing.acquire(blocking=False):
            msg = "Another plugin change is running; try again when it has finished."
            raise HeinzelError(msg)
        try:
            yield
        finally:
            self._changing.release()

    def _record(  # noqa: PLR0913 - one field each, by keyword
        self,
        action: Action,
        distribution: str | None,
        *,
        requirement: str | None = None,
        source: Source | None = None,
        version: str | None = None,
        publisher: str | None = None,
    ) -> None:
        event = PluginEvent(
            at=datetime.now(UTC),
            action=action,
            distribution=distribution,
            requirement=requirement,
            source=source,
            version=version,
            publisher=publisher,
        )
        path = self._history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as history:
            history.write(event.model_dump_json() + "\n")

    def _history_path(self) -> Path:
        return self._kernel.settings.data_dir / HISTORY

    def _changed(self, action: Action, distribution: str | None, candidate: PluginCandidate | None) -> PluginChange:
        """A carried-out change, with the plugins as a fresh `hz` sees them; this process still has the old ones."""
        return PluginChange(
            action=action,
            done=True,
            distribution=distribution,
            candidate=candidate,
            plugins=self._fresh_list(),
            restart_required=True,
            next=[],
        )

    def _fresh_list(self) -> list[PluginInfo]:
        return [
            PluginInfo.model_validate(item)
            for item in json.loads(self._installation.hz("system", "plugin", "list", "--json"))
        ]


def _same(a: str, b: str) -> bool:
    return canonicalize_name(a) == canonicalize_name(b)


SourceKind = Literal["pypi", "git", "path"]


def _is_first_party(name: str, kind: SourceKind | None, url: str | None) -> bool:
    """A first-party name from the repository it is released in, or from a checkout of it."""
    short = short_name(name)
    if short in REFERENCE:
        return url is not None and url.startswith(REFERENCE[short][1])
    if short not in FIRST_PARTY:
        return False
    return kind in {None, "path"} or (url is not None and url.startswith(REPOSITORY))


def _source_of(
    dist: Distribution | None, homepage: str | None = None
) -> tuple[SourceKind | None, str | None, str | None]:
    """Where a distribution was installed from, per its `direct_url.json` (PEP 610), and where a browser shows it.

    No `direct_url.json` means an index (PyPI): its repository from the Project-URL metadata (`homepage`), else its
    PyPI page. Returns the kind, the browser URL (https, no credentials) and the git commit.
    """
    if dist is None:
        return None, None, None
    try:
        text = dist.read_text("direct_url.json")
    except OSError:
        text = None
    if not text:
        return "pypi", browser_url(homepage) or f"https://pypi.org/project/{canonicalize_name(dist.name)}/", None
    try:
        data = cast("dict[str, Any]", json.loads(text))
    except ValueError:
        return None, None, None
    url = str(data.get("url") or "") or None
    subdirectory = str(data.get("subdirectory") or "") or None
    if vcs := cast("dict[str, Any] | None", data.get("vcs_info")):
        commit = str(vcs.get("commit_id") or "") or None
        # The commit before a requested branch, which may have moved since: the link opens the code installed.
        ref = commit or str(vcs.get("requested_revision") or "") or None
        return "git", installed_repository_url(url, ref, subdirectory), commit
    if url is not None and url.startswith("file:"):
        return "path", None, None
    return None, browser_url(url), None
