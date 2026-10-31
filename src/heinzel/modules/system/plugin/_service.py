"""Service of the plugin module: plugins are extra requirements of Heinzelmännchen's uv tool environment (ADR 0006).

Adding one runs its code with your rights, so `add` first shows what it would install (source, version, publisher,
pinned to the commit or release it read) and installs only with `confirm`; every change is recorded in
`<data_dir>/plugins.jsonl` (ADR 0047).
"""

import base64
import contextlib
import hashlib
import json
import re
import shlex
import subprocess
import tempfile
import threading
import tomllib
import urllib.error
from collections.abc import Callable, Collection, Generator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from http import HTTPStatus
from importlib.metadata import Distribution, distributions, entry_points
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlencode, urlsplit

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from heinzel import HeinzelError, Kernel, __version__
from heinzel.kernel import (
    CORE_DISTRIBUTION,
    ENTRY_POINT_GROUP,
    KERNEL_SECTION,
    ConfigEditor,
    DaemonInstallationLifecycle,
    DaemonMutationScope,
    Installation,
    LayerLet,
    ModuleRecord,
    OperationLet,
    PlaybookLet,
    ProviderLet,
    SignalLet,
    ThemeLet,
    subprocess_environment,
)
from heinzel.modules.system._github import GITHUB_API, RateLimitedError
from heinzel.modules.system.plugin._feed import feed_showcase, load_feed, raw_image_size, raw_text, soundscape_hints
from heinzel.modules.system.plugin._models import (
    Action,
    AvailablePlugin,
    PluginCandidate,
    PluginChange,
    PluginEvent,
    PluginInfo,
    PluginKind,
    PluginLayer,
    PluginOperation,
    PluginSearch,
    PluginSettings,
    PluginShowcase,
    Source,
)
from heinzel.modules.system.plugin._receipts import PluginReceipts
from heinzel.modules.system.plugin._showcase import public_showcase, raw_showcase
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
_PUBLISHED_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_PUBLISHED_DIRECTORY = re.compile(r"\.|[A-Za-z0-9_][A-Za-z0-9_./-]{0,200}")
_CATALOG_CACHE: dict[str, list[tuple[str, str, str]]] = {}
"""Last successfully read source metadata; a transient source outage retains its catalog."""
TOPIC = "heinzel-plugin"
GITHUB_SEARCH = "https://api.github.com/search/repositories"
GITHUB_REPOS = "https://api.github.com/repos"
MANIFEST = "heinzel-plugins.toml"
PAGE_SIZE = 100
MAX_CATALOG_BYTES = 512 * 1024
CATALOG_ROW_FIELDS = 3
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


@dataclass
class _PackageMutation:
    scope: DaemonMutationScope
    initial: str
    generation: str | None = None


class PluginService:
    """Lists, adds, removes, enables, disables and scaffolds plugins."""

    def __init__(
        self,
        kernel: Kernel,
        installation: Installation,
        settings: PluginSettings,
        *,
        lifecycle: DaemonInstallationLifecycle | None = None,
    ) -> None:
        """Create the service; the kernel, the installation and the settings are injected."""
        self._kernel = kernel
        self._installation = installation
        self._settings = settings
        self._lifecycle = lifecycle
        self._changing = threading.Lock()
        self._receipts = PluginReceipts(kernel.settings.state_dir)
        self.github_api: JsonGetter = github_getter(settings.github_token)
        """Reads the GitHub API for `add`; tests replace it."""
        self.pypi: JsonGetter = pypi_get
        """Reads PyPI's JSON API for `add`; tests replace it."""
        self.feed: Callable[[], list[dict[str, Any]]] = self._read_feed
        """Reads the published static catalog (no GitHub request); tests replace it."""
        self.raw_text = raw_text
        """Reads a small public file from raw.githubusercontent.com; tests replace it."""
        self.raw_size = raw_image_size
        """The size and type of a public file from raw.githubusercontent.com; tests replace it."""
        self._catalog_warnings: list[str] = []
        self._published_repositories: set[str] = set()
        self.distributions: Mapping[str, Distribution] | None = None
        """Installed plugin distributions by canonical name; read from the entry points when None (tests set it)."""

    def showcase(self, spec: str) -> PluginShowcase:
        """Read a public plugin's immutable screenshots before installation.

        Raises:
            HeinzelError: If the specification or public media is unsafe.
            OSError: If public source metadata cannot be read.
            ValueError: If public metadata is malformed.
        """
        spec = spec.strip()
        resolved = spec
        if spec.replace("-", "").isalnum():
            wanted = canonicalize_name(spec if spec.startswith(PREFIX) else PREFIX + spec)
            matches = [entry for entry in self.search(spec.removeprefix(PREFIX)).plugins if entry.name == wanted]
            if len(matches) > 1:
                message = "Several plugins have that name; preview an exact catalog specification"
                raise HeinzelError(message)
            if matches:
                resolved = matches[0].install_spec
        try:
            return self._preview(parse(resolved))
        except RateLimitedError as error:
            raise HeinzelError(error.message()) from error
        except (OSError, ValueError, KeyError, TypeError) as error:
            message = "The plugin's public previews could not be read. Try again or open its owning repository."
            raise HeinzelError(message) from error

    def _preview(self, source: PluginSource) -> PluginShowcase:
        """Preview a source, spending a GitHub API request only when nothing cheaper can answer.

        Order: the published catalog (if it describes this very revision), then the pinned revision's own files on
        raw.githubusercontent.com, then the GitHub API (to resolve a branch, or to list undeclared soundscapes).
        """
        hints: list[str] = []
        try:
            catalog = self.feed()
            published = feed_showcase(source, catalog)
            hints = soundscape_hints(source, catalog)
        except (OSError, ValueError):
            published = None  # the catalog is an optimisation; GitHub can still answer
        if published is not None:
            return published
        pinned = raw_showcase(source, self.raw_text, self.raw_size, hints)
        if pinned is None:
            return public_showcase(source, self.github_api)
        gallery, discover = pinned
        if not discover:
            return gallery
        try:
            return public_showcase(source, self.github_api)
        except (OSError, ValueError, KeyError, TypeError):
            note = " Soundscape links could not be listed right now."
            return gallery.model_copy(update={"notice": gallery.notice + note})

    def _read_feed(self) -> list[dict[str, Any]]:
        url = self._settings.catalog_feed
        if not url:
            return []
        return load_feed(url, self._kernel.settings.cache_dir)

    # Listing.

    def installed(self, *, fresh: bool = False) -> list[PluginInfo]:
        """Read this process's loaded plugin inventory, or explicitly reconcile the installed environment.

        Raises:
            HeinzelError: If the requested fresh interpreter cannot read the installed environment.
        """
        if fresh:
            # The child uses the default snapshot path; forwarding --fresh would recursively spawn interpreters.
            return self._fresh_list()
        return self._loaded_inventory()

    def _loaded_inventory(self) -> list[PluginInfo]:
        """Describe the plugins and modules already loaded by this kernel."""
        visibility: dict[str, bool | None] = {}
        for event in self.history():
            if not event.distribution:
                continue
            distribution_name = canonicalize_name(event.distribution)
            if event.action == "remove":
                visibility.pop(distribution_name, None)
            elif event.action in {"add", "develop"}:
                visibility[distribution_name] = event.private_repository
        records: dict[str, list[ModuleRecord]] = {}
        enabled: dict[str, bool] = {}
        for record in self._kernel.modules:
            if not record.origin.builtin:
                records.setdefault(canonicalize_name(record.origin.distribution), []).append(record)
                enabled[canonicalize_name(record.origin.distribution)] = True
        for record in self._kernel.blocked:
            if not record.origin.builtin:
                distribution_name = canonicalize_name(record.origin.distribution)
                records.setdefault(distribution_name, []).append(record)
                enabled.setdefault(distribution_name, False)
        distributions = self._plugin_distributions()
        errors = self._errors_by_distribution()
        plugins: list[PluginInfo] = []
        # What the kernel loaded, blocked or failed to load; with `[hz] plugins_enabled` off, that is nothing.
        for plugin_name in sorted(set(records) | set(errors)):
            mine = sorted(records.get(plugin_name, []), key=lambda r: r.path)
            dist = distributions.get(plugin_name)
            origin = mine[0].origin if mine else None
            homepage = origin.url if origin else None
            kind, url, revision = _source_of(dist, homepage)
            lets = [let for record in mine for let in record.module.lets]
            capabilities = sorted({f"{let.adapter}:{let.capability}" for let in lets if isinstance(let, ProviderLet)})
            themes = sorted({let.name for let in lets if isinstance(let, ThemeLet)})
            playbooks = [r.qualname for r in mine if any(isinstance(let, PlaybookLet) for let in r.module.lets)]
            # Signals a theme follows (ADR 0060) are part of the look, not a capability of their own.
            signals = {let.func for let in lets if isinstance(let, SignalLet)}
            features = any(
                isinstance(let, OperationLet) and let.name not in TEMPLATE_OPERATIONS and let.func not in signals
                for let in lets
            )
            plugins.append(
                PluginInfo(
                    distribution=origin.distribution if origin else (dist.name if dist else plugin_name),
                    # The metadata installed now: the kernel read its origin at start, before an upgrade replaced it.
                    version=dist.version if dist else (origin.version if origin else "unknown"),
                    modules=[r.qualname for r in mine],
                    name=short_name(plugin_name),
                    summary=str(dist.metadata.get("Summary") or "") if dist else "",
                    source_kind=kind,
                    source_url=url,
                    revision=revision,
                    homepage=homepage,
                    first_party=_is_first_party(url or homepage),
                    private_repository=visibility.get(plugin_name),
                    enabled=enabled.get(plugin_name, False) and self._kernel.settings.plugins_enabled,
                    error=errors.get(plugin_name),
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

    def candidate(self, spec: str) -> PluginCandidate:
        """What `add` would install for `spec`, read from PyPI or GitHub and pinned where it can be.

        Raises:
            HeinzelError: If the source isn't one `add` takes, or names a package, repository or ref that doesn't exist.
        """
        # Short names resolve through the same metadata-only catalog as Discover, never a code allowlist.
        resolved: str | None = None
        if spec.strip().replace("-", "").isalnum() and not spec.strip().startswith(PREFIX):
            wanted = canonicalize_name(spec.strip().removeprefix(PREFIX))
            matches = [entry for entry in self.search(wanted).plugins if canonicalize_name(entry.short_name) == wanted]
            if len(matches) > 1:
                msg = "Several catalog plugins have that short name; install an exact catalog specification"
                raise HeinzelError(msg)
            if matches:
                resolved = matches[0].install_spec
        source = parse(resolved or spec)
        warnings: list[str] = []
        metadata = Metadata()
        try:
            if source.kind == "pypi":
                metadata = from_pypi(source, self.pypi)
            elif source.github is not None:
                metadata = from_github(source, self.github_api)
                source = replace(source, subdirectory=metadata.subdirectory or source.subdirectory)
            else:
                warnings.append(
                    f"{urlsplit(source.url or '').hostname} isn't GitHub: its name, version and publisher can't be "
                    "read before installing; add it only if you trust that repository."
                )
        # Isolation boundary: an unreachable PyPI or GitHub leaves the source unverified, which the warning says.
        except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            warnings.append(f"What it is couldn't be read ({reason}): only the source below is known.")
        if source.requirement is not None and (source.requirement.extras or source.requirement.marker is not None):
            msg = f"Add {source.requirement.name} without extras or environment markers; a plugin brings what it needs."
            raise HeinzelError(msg)
        distribution = metadata.name or source.name
        _refuse_core_distribution(distribution)
        _require_git_distribution(source.kind, distribution)
        page: str | None
        if source.kind == "pypi" and source.requirement is not None:
            pinned = f"{distribution}=={metadata.version}" if metadata.version else str(source.requirement)
            requirement, page = pinned, metadata.page or f"https://pypi.org/project/{distribution}/"
        else:
            requirement, page = source.git_requirement(distribution, metadata.revision), metadata.page or source.url
        self._check(spec, source, metadata, requirement)
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
            publisher=MAINTAINERS if _is_first_party(source.url) else metadata.publisher,
            first_party=_is_first_party(source.url),
            private=metadata.private,
            revision=metadata.revision,
            installed_version=installed,
            warnings=warnings,
        )

    def add(self, spec: str, *, confirm: bool = False, operation_id: str | None = None) -> PluginChange:
        """Show what `spec` installs; with `confirm`, install exactly that, record it and list the plugins after.

        Raises:
            HeinzelError: If the source is refused, unknown, or installing fails.
        """
        if operation_id is not None:
            if not confirm:
                msg = "A plugin operation receipt can only be used to confirm an add."
                raise HeinzelError(msg)
            receipt = self._receipts.get(operation_id)
            candidate = receipt.candidate
            if spec not in {candidate.spec, candidate.requirement}:
                msg = "This plugin operation receipt belongs to a different preview."
                raise HeinzelError(msg)
            if receipt.status == "completed" and receipt.result:
                return receipt.result
            if receipt.status == "running":
                msg = "This plugin operation is still running; check its receipt before continuing."
                raise HeinzelError(msg)
            _refuse_core_distribution(candidate.distribution)
            _require_git_distribution(candidate.source, candidate.distribution)
            _refuse_core_distribution(parse(candidate.requirement).name)
            if receipt.status == "unknown":
                # A read-only receipt lookup may infer this from a dead owner. Persist it on this explicit write path
                # before refusing replay, so status reads remain truthful to their read-only operation metadata.
                receipt = self._receipts.claim(operation_id)
                if receipt.status == "completed" and receipt.result:
                    return receipt.result
                msg = "This plugin operation has an unknown result and must not be replayed."
                raise HeinzelError(msg)
            return self._confirm_receipt(operation_id, candidate)
        candidate = self.candidate(spec)
        if not confirm:
            handle = candidate.spec if candidate.first_party else candidate.requirement
            receipt = self._receipts.create(candidate)
            return PluginChange(
                action="add",
                done=False,
                distribution=candidate.distribution,
                candidate=candidate,
                plugins=self.installed(),
                restart_required=False,
                # A first-party plugin is added by the name asked for: its git requirement is refused as a spec.
                next=[f"hz system plugin add {shlex.quote(handle)} --confirm --operation-id {receipt.operation_id}"],
                operation_id=receipt.operation_id,
            )
        with self._mutation() as mutation, self._receipts.installing(), self._exclusive():
            before = self._host_list() if _requires_change(candidate) else []
            generation = self._run_package_change(
                self.add_command(candidate.requirement),
                f"Failed to add plugin {spec!r}.",
                f"Adding plugin {candidate.distribution or spec}",
                self._git_environment(),
                context=(mutation, ()),
            )
            installed = self._verify_added(candidate, before)
            self._verify_candidate_generation(mutation)
            change = self._changed("add", installed.distribution, candidate)
            self._verify_candidate_generation(mutation)
            self._record(
                "add",
                installed.distribution,
                requirement=candidate.requirement,
                source=candidate.source,
                version=candidate.version,
                publisher=candidate.publisher,
                private_repository=candidate.private,
            )
            self._remember_package_change(generation)
            return change

    def operation(self, operation_id: str) -> PluginOperation:
        """Read the durable status of a preview receipt without repeating its mutation."""
        return self._receipts.get(operation_id)

    def _confirm_receipt(self, operation_id: str, candidate: PluginCandidate) -> PluginChange:
        claimed = False
        try:
            with self._receipts.installing(), self._exclusive():
                receipt = self._receipts.claim(operation_id)
                if receipt.status != "running":
                    if receipt.status == "completed" and receipt.result:
                        return receipt.result
                    _raise_not_replayable(receipt.status)
                claimed = True
                with self._mutation() as mutation:
                    before = self._host_list() if _requires_change(candidate) else []
                    generation = self._run_package_change(
                        self.add_command(candidate.requirement),
                        f"Failed to add plugin {candidate.spec!r}.",
                        f"Adding plugin {candidate.distribution or candidate.spec}",
                        self._git_environment(),
                        context=(mutation, ()),
                    )
                    installed = self._verify_added(candidate, before)
                    self._verify_candidate_generation(mutation)
                    result = self._changed("add", installed.distribution, candidate)
                    self._verify_candidate_generation(mutation)
                    self._record(
                        "add",
                        installed.distribution,
                        requirement=candidate.requirement,
                        source=candidate.source,
                        version=candidate.version,
                        publisher=candidate.publisher,
                        private_repository=candidate.private,
                    )
                    self._remember_package_change(generation)
                completed = self._receipts.complete(operation_id, result)
                if completed.result is None:
                    _raise_missing_result()
                return cast("PluginChange", completed.result)
        except BaseException:
            if claimed:
                self._receipts.unknown(operation_id)
            raise

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
        _refuse_core_distribution(name)
        with self._mutation() as mutation, self._receipts.installing(), self._exclusive():
            generation = self._run_package_change(
                self.add_command(f"{name} @ {directory.as_uri()}"),
                f"Failed to add plugin {path}.",
                f"Adding plugin {name}",
                context=(mutation, ()),
            )
            self._verify_developed(directory, name, str(version) if version is not None else None)
            self._verify_candidate_generation(mutation)
            change = self._changed("develop", name, None)
            self._verify_candidate_generation(mutation)
            self._record("develop", name, requirement=str(directory), source="path", version=version and str(version))
            self._remember_package_change(generation)
            return change

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
        with self._mutation() as mutation, self._receipts.installing(), self._exclusive():
            version = next((p.version for p in self.installed() if _same(p.distribution, distribution)), None)
            generation = self._run_package_change(
                self.remove_command(distribution),
                f"Failed to remove plugin {distribution!r}.",
                f"Removing {distribution}",
                self._git_environment(),  # reinstalling fetches the other git plugins again, private ones too
                context=(mutation, (canonicalize_name(distribution),)),
            )
            if any(_same(item.distribution, distribution) for item in self._host_list(plugins_only=False)):
                msg = "The requested plugin is still installed; nothing was recorded as a successful removal."
                raise HeinzelError(msg)
            self._verify_candidate_generation(mutation)
            change = self._changed("remove", canonicalize_name(distribution), None)
            self._verify_candidate_generation(mutation)
            self._record("remove", canonicalize_name(distribution), version=version)
            self._remember_package_change(generation)
            return change

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

    def new(  # noqa: PLR0913 - mirrors the independently selectable Copier options
        self,
        destination: Path,
        template: str = TEMPLATE,
        ref: str | None = None,
        data: Sequence[str] = (),
        *,
        defaults: bool = False,
        layout: str = "standalone",
        preset: str = "minimal",
    ) -> Path:
        """Scaffold a plugin repository from the Heinzelmännchen copier template.

        Copier asks for every answer missing from `data` (`key=value`), so with all answers given it runs unattended.
        The template is pinned to this Heinzelmännchen's release tag unless `ref` says otherwise.

        Raises:
            HeinzelError: If copier fails.
        """
        ref = ref or (f"v{__version__}" if __version__ != "0.0.0" else "HEAD")
        if layout == "collection" and preset == "governed":
            msg = "Collection plugins reuse repository governance; choose --preset contributions."
            raise HeinzelError(msg)
        command = [self._installation.uv, "tool", "run", "copier", "copy", "--vcs-ref", ref]
        if defaults:
            command.append("--defaults")
        for answer in (f"layout={layout}", f"preset={preset}"):
            command += ["--data", answer]
        for answer in data:
            command += ["--data", answer]
        command += [template, str(destination)]
        # Copier asks questions, so it gets the terminal instead of a spinner.
        interactive = subprocess.run(command, check=False, env=subprocess_environment())  # noqa: S603 - trusted parts, no shell; interactive: no timeout
        if interactive.returncode != 0:
            msg = f"Failed to scaffold a plugin into {destination}."
            raise HeinzelError(msg)
        return destination

    @staticmethod
    def sdk(destination: Path) -> list[Path]:
        """Export this installed core's versioned view SDK without requiring its source checkout.

        Raises:
            HeinzelError: If the destination already exists or cannot be written.
        """
        try:
            destination.mkdir(parents=True, exist_ok=False)
            source = files("heinzel.kernel").joinpath("assets/plugin-sdk/v1")
            written: list[Path] = []
            for asset in source.iterdir():
                target = destination / asset.name
                target.write_bytes(asset.read_bytes())
                written.append(target)
        except OSError as exc:
            msg = "Choose a new writable SDK directory; existing files are never overwritten"
            raise HeinzelError(msg) from exc
        return sorted(written)

    def search(self, text: str = "") -> PluginSearch:
        """Discover projects from configured repository directories and repositories carrying the plugin topic."""
        installed = {canonicalize_name(p.distribution) for p in self.installed()}
        words = text.casefold().split()
        warnings: list[str] = []
        self._catalog_warnings = []
        sources = [
            {
                "full_name": name,
                "html_url": f"https://github.com/{name}",
                "name": name.rsplit("/", 1)[-1],
                "default_branch": "main",
            }
            for name in self._settings.catalog_sources
        ]
        try:
            sources.extend(self._github_repositories(text))
        except RateLimitedError as error:
            warnings.append(f"Community search is paused. {error.message()}")
        except (OSError, ValueError, KeyError, TypeError):
            warnings.append("Topic search is unavailable; showing configured catalogs and cached metadata.")
        found: dict[tuple[str, str], AvailablePlugin] = {}
        seen: set[str] = set()
        for entry in self._published_plugins(installed):
            if all(word in f"{entry.name} {entry.description}".casefold() for word in words):
                found.setdefault((entry.name, entry.install_spec), entry)
        seen.update(self._published_repositories)
        for repository in sources:
            identity = str(repository.get("full_name", ""))
            if identity in seen or repository.get("archived"):
                continue
            seen.add(identity)
            for entry in self._community_plugins(repository, installed):
                if all(word in f"{entry.name} {entry.description}".casefold() for word in words):
                    found.setdefault((entry.name, entry.install_spec), entry)
        return PluginSearch(
            plugins=sorted(found.values(), key=lambda entry: (entry.community, -(entry.stars or 0), entry.name)),
            warnings=warnings + self._catalog_warnings,
        )

    def _published_plugins(self, installed: Collection[str]) -> list[AvailablePlugin]:
        """First-party plugins from the published static catalog: no GitHub request, and public sources only."""
        self._published_repositories = set()
        try:
            entries = self.feed()
        except (OSError, ValueError):
            return []  # GitHub answers instead
        plugins: list[AvailablePlugin] = []
        for entry in entries:
            source = entry.get("source")
            if not isinstance(source, dict) or source.get("visibility") != "public":
                continue
            repository, name = source.get("repository"), entry.get("name")
            directory = entry.get("directory")
            if (
                not isinstance(repository, str)
                or not isinstance(name, str)
                or not isinstance(directory, str)
                or not name.startswith(PREFIX)
                or not _PUBLISHED_REPOSITORY.fullmatch(repository)
                or not _PUBLISHED_DIRECTORY.fullmatch(directory)
            ):
                continue
            url = f"https://github.com/{repository}"
            path = "" if directory == "." else directory
            self._published_repositories.add(repository)
            plugins.append(
                AvailablePlugin(
                    name=name,
                    short_name=name.removeprefix(PREFIX),
                    description=str(entry.get("summary") or "")[:300],
                    source="first-party" if _is_first_party(url) else "github",
                    community=not _is_first_party(url),
                    install_spec=f"{name} @ git+{url}" + (f"#subdirectory={path}" if path else ""),
                    url=f"{url}/tree/main/{path}" if path else url,
                    repository_url=repository_url(f"git+{url}"),
                    private_repository=False,
                    stars=0,
                    installed=canonicalize_name(name) in installed,
                )
            )
        return plugins

    def _github_repositories(self, text: str) -> list[dict[str, Any]]:
        """Repositories with the topic heinzel-plugin that match `text`.

        Raises:
            OSError: If GitHub cannot be reached or answers with an error, e.g. when rate-limited.
            ValueError: If the answer is not JSON.
            KeyError: If the answer has no list of repositories.
            TypeError: If the answer has an unexpected shape.
        """
        repositories: list[dict[str, Any]] = []
        page = 1
        while True:
            query = urlencode(
                {"q": f"topic:{TOPIC} {text}".strip(), "sort": "stars", "per_page": PAGE_SIZE, "page": page}
            )
            items = self._catalog_json(f"{GITHUB_SEARCH}?{query}")["items"]
            if not isinstance(items, list):
                msg = "Expected a list of plugin repositories"
                raise TypeError(msg)
            values = cast("list[object]", items)
            repositories.extend(cast("list[dict[str, Any]]", [item for item in values if isinstance(item, dict)]))
            if len(values) < PAGE_SIZE:
                return repositories
            page += 1

    def _community_plugins(self, repository: dict[str, Any], installed: Collection[str]) -> list[AvailablePlugin]:
        """The plugins of one repository with the topic: one per entry of a monorepo, else the repository itself."""
        url = repository["html_url"]
        stars = repository.get("stargazers_count") or 0
        private = repository.get("private")
        visibility = private if isinstance(private, bool) else None
        entries = self._monorepo_plugins(repository)
        if not entries:
            full_name = repository.get("full_name")
            text = self._file(f"{GITHUB_REPOS}/{full_name}/contents/pyproject.toml") if full_name else None
            project = _table(_toml(text), "project") if text else {}
            points = _table(project, "entry-points")
            if not _table(points, ENTRY_POINT_GROUP):
                return []
            name = project.get("name")
            if not isinstance(name, str):
                return []
            entries = [(name, "", str(project.get("description") or ""))]
        branch = repository.get("default_branch") or "HEAD"
        return [
            AvailablePlugin(
                name=name,
                short_name=name.removeprefix("heinzel-"),
                description=summary,
                source="first-party" if _is_first_party(url) else "github",
                community=not _is_first_party(url),
                install_spec=f"{name} @ git+{url}" + (f"#subdirectory={path}" if path else ""),
                url=f"{url}/tree/{branch}/{path}" if path else url,
                repository_url=repository_url(f"git+{url}"),
                private_repository=visibility,
                stars=stars,
                installed=canonicalize_name(name) in installed,
            )
            for name, path, summary in entries
        ]

    def _monorepo_plugins(self, repository: dict[str, Any]) -> list[tuple[str, str, str]]:
        """(distribution, path, summary) of each plugin a repository declares; empty for a single-plugin repository.

        Projects declare their identity and entry points in `plugins/*/pyproject.toml`. A separate manifest
        cannot hide directories added later. Discovery reads metadata without executing source.
        """
        full_name = repository.get("full_name")
        if not full_name:
            return []
        api = f"{GITHUB_REPOS}/{full_name}/contents"
        if str(full_name) not in _CATALOG_CACHE:
            self._restore_catalog(str(full_name))
        listing = self._catalog_listing(str(full_name), api)
        if listing is None:
            return _CATALOG_CACHE.get(str(full_name), [])
        previous = {entry[1]: entry for entry in _CATALOG_CACHE.get(str(full_name), [])}
        found: list[tuple[str, str, str]] = []
        for item in sorted(_dicts(listing), key=lambda i: str(i.get("name"))):
            if item.get("type") == "dir":
                entry = self._catalog_project(str(full_name), api, f"plugins/{item.get('name')}", previous)
                if entry is not None:
                    found.append(entry)
        self._save_catalog(str(full_name), found)
        return found

    def _catalog_json(self, url: str) -> Any:  # noqa: ANN401 - GitHub's free-form metadata
        prefix = f"{GITHUB_API}/"
        if not url.startswith(prefix):
            msg = "Catalog metadata must come from the GitHub API"
            raise ValueError(msg)
        return self.github_api(url.removeprefix(prefix))

    def _catalog_listing(self, repository: str, api: str) -> object:
        try:
            listing: object = self._catalog_json(f"{api}/plugins")
        except LookupError:
            return []
        except (OSError, ValueError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code == HTTPStatus.NOT_FOUND:
                return []
            self._catalog_warnings.append(
                f"Catalog {repository} is unavailable; retaining its last successful metadata."
            )
            return None
        if not isinstance(listing, list):
            self._catalog_warnings.append(
                f"Catalog {repository} returned invalid metadata; retaining its last entries."
            )
            return None
        return cast("list[object]", listing)

    def _catalog_project(
        self, repository: str, api: str, path: str, previous: dict[str, tuple[str, str, str]]
    ) -> tuple[str, str, str] | None:
        text = self._file(f"{api}/{path}/pyproject.toml", missing_is_empty=True)
        if text is None:
            self._catalog_warnings.append(
                f"Catalog {repository}: metadata for {path} is unavailable; retaining its last entry."
            )
            return previous.get(path)
        project = _table(_toml(text), "project")
        dist = project.get("name")
        if (
            isinstance(dist, str)
            and dist.startswith("heinzel-")
            and _table(_table(project, "entry-points"), ENTRY_POINT_GROUP)
        ):
            return dist, path, str(project.get("description") or "")
        return None

    def _file(self, url: str, *, missing_is_empty: bool = False) -> str | None:
        """The text of a file from GitHub's contents API; None if it is not there or not readable."""
        try:
            answer: object = self._catalog_json(url)
            if isinstance(answer, dict):
                fields = cast("dict[str, object]", answer)
                if fields.get("encoding") == "base64":
                    return base64.b64decode(str(fields["content"])).decode()
        except KeyError:
            return None
        except (LookupError, OSError, ValueError) as exc:
            if missing_is_empty and (
                isinstance(exc, LookupError)
                or (isinstance(exc, urllib.error.HTTPError) and exc.code == HTTPStatus.NOT_FOUND)
            ):
                return ""
            return None
        return None

    def _catalog_path(self, repository: str) -> Path:
        return (
            self._kernel.settings.cache_dir
            / "plugin-catalog"
            / (hashlib.sha256(repository.encode()).hexdigest() + ".json")
        )

    def _restore_catalog(self, repository: str) -> None:
        try:
            with self._catalog_path(repository).open("rb") as handle:
                raw = handle.read(MAX_CATALOG_BYTES + 1)
            if len(raw) > MAX_CATALOG_BYTES:
                return
            value: object = json.loads(raw)
            if not isinstance(value, list):
                return
            entries = cast("list[object]", value)
            parsed: list[tuple[str, str, str]] = []
            for entry in entries:
                if not isinstance(entry, list):
                    return
                row = cast("list[object]", entry)
                if len(row) != CATALOG_ROW_FIELDS or not all(isinstance(item, str) for item in row):
                    return
                parsed.append(cast("tuple[str, str, str]", tuple(row)))
            _CATALOG_CACHE[repository] = parsed
        except (OSError, ValueError, TypeError):
            return

    def _save_catalog(self, repository: str, entries: list[tuple[str, str, str]]) -> None:
        _CATALOG_CACHE[repository] = entries
        temporary: Path | None = None
        try:
            path = self._catalog_path(repository)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(json.dumps(entries))
            temporary.replace(path)
        except OSError:
            return  # Discovery remains useful when an optional cache cannot be saved.
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    # Helpers.

    @staticmethod
    def _check(spec: str, source: PluginSource, metadata: Metadata, requirement: str) -> None:
        """Refuse metadata that would install something other than what is shown.

        Raises:
            HeinzelError: If the plugin's own name differs from the one asked for, the requirement carries extras
                or markers.
        """
        if source.name and metadata.name and canonicalize_name(source.name) != metadata.name:
            msg = f"{spec!r} asks for {source.name}, but the repository's pyproject.toml names {metadata.name}."
            raise HeinzelError(msg)
        # An unnamed git URL (a host whose metadata can't be read) is no PEP 508 requirement; uv names it on install.
        parsed = Requirement(requirement) if source.kind == "pypi" or " @ " in requirement else None
        if parsed is not None and (parsed.extras or parsed.marker is not None):
            msg = f"Add {parsed.name} without extras or environment markers; a plugin brings what it needs."
            raise HeinzelError(msg)

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
        private_repository: bool | None = None,
    ) -> None:
        event = PluginEvent(
            at=datetime.now(UTC),
            action=action,
            distribution=distribution,
            requirement=requirement,
            source=source,
            version=version,
            publisher=publisher,
            private_repository=private_repository,
        )
        path = self._history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as history:
            history.write(event.model_dump_json() + "\n")

    def _history_path(self) -> Path:
        return self._kernel.settings.data_dir / HISTORY

    @contextlib.contextmanager
    def _mutation(self) -> Generator[_PackageMutation | None]:
        """Hold the trusted lifecycle and original tool authority through the complete change.

        Yields:
            The scoped tool mutation, or None for a non-tool development environment.

        Raises:
            HeinzelError: If lifecycle authority or the resulting generation is unavailable.
            OSError: If the continuity record cannot be written.
        """
        if not self._installation.is_tool:
            yield None
            return
        if self._lifecycle is None:
            msg = "The daemon lifecycle boundary is unavailable; the plugin was not changed."
            raise HeinzelError(msg)
        with self._lifecycle.mutation(
            self._installation.startup_state_matches, self._installation.rollback_identity
        ) as scope:
            plan = scope.capture()
            try:
                with self._installation.tool_transaction():
                    mutation = _PackageMutation(scope, self._installation.startup_fingerprint())
                    yield mutation
                    self._complete_mutation(mutation)
                    scope.request(plan)
            except (HeinzelError, OSError):
                with contextlib.suppress(HeinzelError, OSError):
                    scope.abort_unchanged()
                raise

    def _complete_mutation(self, mutation: _PackageMutation) -> None:
        if mutation.generation is None:
            msg = "The plugin installation was not verified; explicit recovery is required."
            raise HeinzelError(msg)
        if mutation.scope.checkpoint_matched:
            self._installation.record_startup_state(expected=mutation.generation)
        mutation.scope.verified_complete(mutation.generation)

    def _run_package_change(
        self,
        command: list[str],
        failure: str,
        activity: str,
        environment: Mapping[str, str] | None = None,
        *,
        context: tuple[_PackageMutation | None, tuple[str, ...]] = (None, ()),
    ) -> str | None:
        """Capture the changed tool before independently checking its installed identity and runtime.

        Raises:
            HeinzelError: If installation or the host inspection fails.
        """
        # A genuine SDK tool is not necessarily the standard app. Only its verified installer may seed continuity.
        mutation, forbidden = context
        managed = self._installation.is_tool and self._installation.startup_state_matches()
        if mutation is None:
            self._installation.run(command, failure, activity, environment)
            return self._installation.startup_fingerprint() if managed else None
        policy = self._installation.capture_tool_constraint_policy()
        version = self._installation.installed_core_version()
        guard = f"heinzel=={version}"
        constraints = (*self._installation.tool_constraints(), guard)
        python = command.index("--python")
        selected = [
            command[python + 2],
            *(command[index + 1] for index, part in enumerate(command) if part == "--with"),
        ]
        self._installation.validate_install_constraints(selected, constraints)
        if self._installation.startup_fingerprint() != mutation.initial:
            msg = "The plugin installation changed before mutation; inspect and retry."
            raise HeinzelError(msg)
        self._installation.run(command, failure, activity, environment, additional_constraints=(guard,))
        generation = self._installation.startup_fingerprint()
        self._installation.verify_installed_requirements(
            selected, expected_version=version, forbidden=forbidden, constraints=constraints
        )
        if self._installation.startup_fingerprint() != generation:
            msg = "The plugin installation changed during verification; explicit recovery is required."
            raise HeinzelError(msg)
        mutation.generation = self._installation.remove_transient_tool_constraint(
            expected=generation, original=policy, added=guard
        )
        return None

    def _remember_package_change(self, generation: str | None) -> None:
        """Bind only a successfully checked and recorded package change to its original observation.

        Raises:
            HeinzelError: If the checked installation generation changed.
            OSError: If the private continuity record cannot be persisted.
        """
        if generation is not None:
            self._installation.record_startup_state(expected=generation)

    def _verify_candidate_generation(self, mutation: _PackageMutation | None) -> None:
        if mutation is not None and (
            mutation.generation is None or self._installation.startup_fingerprint() != mutation.generation
        ):
            msg = "The plugin installation changed during verification; explicit recovery is required."
            raise HeinzelError(msg)

    def _verify_added(self, candidate: PluginCandidate, before: list[PluginInfo]) -> PluginInfo:
        """Verify the requested distribution before recording or reporting a successful add.

        Raises:
            HeinzelError: If the requested plugin is absent after the installer returned success.
        """
        after = self._host_list()
        previous = {(item.distribution, item.version, item.revision, item.source_url) for item in before}
        requires_change = _requires_change(candidate)
        matches = [
            item
            for item in after
            if _matches_candidate(item, candidate)
            and (
                not requires_change or (item.distribution, item.version, item.revision, item.source_url) not in previous
            )
        ]
        if len(matches) != 1:
            msg = (
                "The requested plugin identity is not present after installation; "
                "nothing was recorded as a successful add."
            )
            raise HeinzelError(msg)
        return matches[0]

    def _host_distributions(self) -> list[Distribution]:
        """Read selected-prefix metadata without running its Python or providers."""
        roots = [str(path) for path in self._installation.prefix.glob("lib/python*/site-packages")]
        return list(distributions(path=roots))

    def _host_list(self, *, plugins_only: bool = True) -> list[PluginInfo]:
        """Read only facts needed for post-install identity checks before any new target execution.

        Raises:
            HeinzelError: If installed plugin metadata cannot be inspected.
        """
        try:
            result: list[PluginInfo] = []
            for distribution in self._host_distributions():
                if plugins_only and not any(entry.group == ENTRY_POINT_GROUP for entry in distribution.entry_points):
                    continue
                name = canonicalize_name(distribution.name)
                kind, url, revision = _source_of(distribution)
                result.append(
                    PluginInfo(
                        distribution=name,
                        version=distribution.version,
                        modules=[],
                        name=short_name(name),
                        source_kind=kind,
                        source_url=url,
                        revision=revision,
                    )
                )
        except (OSError, ValueError, TypeError, KeyError) as exc:
            msg = "The installed plugin metadata could not be inspected safely; no successful change was recorded."
            raise HeinzelError(msg) from exc
        return result

    def _verify_developed(self, directory: Path, name: str, version: str | None) -> None:
        """Require the requested local source and declared plugin identity before executing the changed target.

        Raises:
            HeinzelError: If local installation did not leave exactly that source and identity.
        """
        try:
            matches = [
                distribution
                for distribution in self._host_distributions()
                if _same(distribution.name, name)
                and (version is None or Version(distribution.version) == Version(version))
                and any(entry.group == ENTRY_POINT_GROUP for entry in distribution.entry_points)
            ]
            document: object = json.loads(matches[0].read_text("direct_url.json") or "{}") if len(matches) == 1 else {}
            provenance = cast("dict[str, Any]", document) if isinstance(document, dict) else {}
            if provenance.get("url") == directory.as_uri() and isinstance(provenance.get("dir_info"), dict):
                return
        except (OSError, ValueError, TypeError, KeyError) as exc:
            msg = "The installed local plugin source could not be inspected; no successful change was recorded."
            raise HeinzelError(msg) from exc
        msg = "The requested local plugin source is not present after installation; no successful change was recorded."
        raise HeinzelError(msg)

    def _changed(self, action: Action, distribution: str | None, candidate: PluginCandidate | None) -> PluginChange:
        """A carried-out change, with the plugins as a fresh `hz` sees them; this process still has the old ones.

        The fresh `hz` also removes skill exports whose skill went or changed (ADR 0061); it never adds one, and a
        failure there is a next step, not a failed change.

        Raises:
            HeinzelError: If fresh discovery fails.
        """
        change = PluginChange(
            action=action,
            done=True,
            distribution=distribution,
            candidate=candidate,
            plugins=self._fresh_list(),
            restart_required=True,
            next=[],
        )
        if action in {"add", "develop"} and distribution is not None:
            # Runtime discovery precedes history persistence, so project this operation's verified source visibility.
            for plugin in change.plugins:
                if _same(plugin.distribution, distribution):
                    plugin.private_repository = candidate.private if candidate is not None else None
        try:
            self._installation.hz("system", "skill", "export", "--sync", "--json")
        except HeinzelError:
            change.next.append("hz system skill export --sync")
        return change

    def _fresh_list(self) -> list[PluginInfo]:
        return [
            PluginInfo.model_validate(item)
            for item in json.loads(self._installation.hz("system", "plugin", "list", "--json"))
        ]


def _raise_not_replayable(status: str) -> None:
    msg = f"This plugin operation is {status}; it will not be replayed."
    raise HeinzelError(msg)


def _refuse_core_distribution(distribution: str | None) -> None:
    """Keep core changes on the explicit update path, before plugin mutation or receipt claim.

    Raises:
        HeinzelError: If the proposed distribution is the core.
    """
    if distribution is not None and canonicalize_name(distribution) == CORE_DISTRIBUTION:
        msg = "Heinzelmännchen itself is updated with `hz system update`, not as a plugin."
        raise HeinzelError(msg)


def _require_git_distribution(source: str, distribution: str | None) -> None:
    """Require an explicit distribution when metadata cannot establish a repository's identity.

    Raises:
        HeinzelError: If a git repository has no known distribution name.
    """
    if source == "git" and distribution is None:
        msg = (
            "The repository's distribution name could not be verified. Supply its actual package name as a named "
            "requirement, for example `heinzel-example @ git+https://example.com/repository`."
        )
        raise HeinzelError(msg)


def _raise_missing_result() -> None:
    msg = "The plugin operation finished without a stored result."
    raise HeinzelError(msg)


def _dicts(value: object) -> list[dict[str, object]]:
    """The objects in a free-form JSON or TOML list; anything else yields none."""
    if not isinstance(value, list):
        return []
    return [cast("dict[str, object]", item) for item in cast("list[object]", value) if isinstance(item, dict)]


def _toml(text: str | None) -> dict[str, object]:
    """A TOML document as a table; empty if it is absent or invalid."""
    if text is None:
        return {}
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}


def _table(document: dict[str, object], key: str) -> dict[str, object]:
    """The table under `key`; empty if there is none."""
    value = document.get(key)
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _known_revision(candidate: PluginCandidate) -> str | None:
    """A resolved revision or an explicitly requested commit, never a guessed branch/tag identity."""
    if candidate.revision or candidate.source != "git":
        return candidate.revision
    ref = parse(candidate.requirement).ref
    return ref.lower() if ref and re.fullmatch(r"[a-fA-F0-9]{7,40}", ref) else None


def _requires_change(candidate: PluginCandidate) -> bool:
    """Unresolved names or Git refs need a fresh change as well as a matching recorded origin."""
    return candidate.distribution is None or (candidate.source == "git" and _known_revision(candidate) is None)


def _matches_git_origin(plugin: PluginInfo, candidate: PluginCandidate) -> bool:
    """A known repository and subdirectory must match actual PEP 610 provenance after installation."""
    if candidate.source != "git":
        return True
    source = parse(candidate.requirement)
    revision = _known_revision(candidate)
    expected = installed_repository_url(source.url, plugin.revision, source.subdirectory)
    return (
        plugin.source_kind == "git"
        and plugin.revision is not None
        and plugin.source_url == expected
        and (revision is None or plugin.revision.startswith(revision))
    )


def _matches_candidate(plugin: PluginInfo, candidate: PluginCandidate) -> bool:
    """Check the full known installed identity; a matching existing identity remains idempotent."""
    try:
        requested = Requirement(candidate.requirement) if candidate.source == "pypi" else None
        return (
            (candidate.distribution is None or _same(plugin.distribution, candidate.distribution))
            and (candidate.version is None or Version(plugin.version) == Version(candidate.version))
            and (requested is None or (plugin.source_kind == "pypi" and Version(plugin.version) in requested.specifier))
            and _matches_git_origin(plugin, candidate)
        )
    except (InvalidVersion, InvalidRequirement):
        return False


def _same(a: str, b: str) -> bool:
    return canonicalize_name(a) == canonicalize_name(b)


SourceKind = Literal["pypi", "git", "path"]


def _is_first_party(url: str | None) -> bool:
    """Only exact maintainer repositories establish provenance, never a package name."""
    if not url:
        return False
    try:
        parsed = urlsplit(url)
        path = parsed.path[1:].lower().split("/")
        return (
            parsed.scheme == "https"
            and parsed.hostname == "github.com"
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
            and len(path) > 1
            and (path[0], path[1].removesuffix(".git"))
            in {("helmut-hoffer-von-ankershoffen", "heinzel"), ("helmut-hoffer-von-ankershoffen", "heinzel-plugins")}
        )
    except ValueError:
        return False


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
