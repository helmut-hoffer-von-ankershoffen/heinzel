"""Newer stable releases of installed plugins, for automatic updates (ADR 0116, ADR 0137).

A plugin whose receipt says it follows the package index is asked cheaply: one GET of PyPI's JSON for its project. A
plugin pinned to a commit of a GitHub repository is asked through its release tags, `<distribution>/vX.Y.Z` (ADR
0094): one listing of its tags, and for the newest one the commit it names and when. A plugin from a git branch, a
local folder or a URL is left out; `hz system update` still refreshes it when an update runs for another reason.

What one plugin needs of another is read from metadata, never by running it (`Requires-Dist`, or the project table of
the tagged commit): a plugin moves only together with the plugins its new release needs newer, and is held back, in
one line, when one of them can't move. That never holds hz or an unrelated plugin.
"""

import json
import re
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from packaging.markers import UndefinedEnvironmentName
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from heinzel import __version__
from heinzel.kernel import CorePin, Installation, Kernel, PluginNews, PluginRelease, PluginReleases
from heinzel.modules.system._announced import plugin_requires
from heinzel.modules.system.plugin._sources import JsonGetter, PluginSource, _github_project_at, github_getter

PYPI = "https://pypi.org"
MAX_BYTES = 4 * 1024 * 1024
MAX_TAGS = 1000
"""At most this many release tags of one plugin are read; a repository with more is read only that far."""
COMMIT = re.compile(r"^[0-9a-f]{40}$")

type Fetch = Callable[[str, float], Any]
type Requires = Callable[[str], list[str]]


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
    """Asks PyPI for each plugin from the package index and GitHub for each one pinned to a commit; stable only."""

    def __init__(
        self,
        installation: Installation,
        fetch: Fetch = _fetch,
        github: JsonGetter | None = None,
        requires: Requires | None = None,
    ) -> None:
        """Ask about the plugins in `installation`'s receipt: PyPI through `fetch`, GitHub through `github`.

        `requires` reads what an installed plugin needs (its `Requires-Dist`); by default from the tool folder.
        """
        self._installation = installation
        self._fetch = fetch
        self._github = github
        self._requires = requires or self._installed_requires

    def _installed_requires(self, name: str) -> list[str]:
        return plugin_requires(self._installation.prefix.resolve(strict=True), name)

    def _get(self) -> JsonGetter:
        if self._github is None:
            self._github = github_getter(None)
        return self._github

    def newer(self, timeout: float) -> dict[str, str]:
        """Each plugin whose latest stable release (unyanked, or tagged) is newer than the installed one, mapped to it.

        Raises:
            HeinzelError: If the installed versions can't be read safely (from `Installation`).
        """
        return {name: release.version for name, release in self.newer_releases(timeout).items()}

    def newer_releases(self, timeout: float) -> dict[str, PluginRelease]:
        """Like `newer`, with when each release was uploaded or tagged, and the commit a tag names.

        Raises:
            HeinzelError: If the installed versions can't be read safely (from `Installation`).
        """
        return self._scan(timeout)[0]

    def _scan(self, timeout: float) -> tuple[dict[str, PluginRelease], list[str]]:
        """The newer releases, and the plugins pinned to a GitHub commit whose repository tags their releases."""
        indexed: dict[str, Requirement] = {}
        tagged: dict[str, tuple[str, str]] = {}
        for pin in self._installation.plugin_pins():
            try:
                requirement = Requirement(pin.requirement)
            except InvalidRequirement:
                continue
            name = canonicalize_name(requirement.name)
            if pin.kind == "index":
                indexed[name] = requirement
            elif (repository := _github(pin)) is not None:
                tagged[name] = repository
        if not indexed and not tagged:
            return {}, []
        installed = self._installation.installed_versions([*indexed, *tagged])
        newer = self._indexed(indexed, installed, timeout)
        followed: list[str] = []
        for plugin, repository in tagged.items():
            tags = self._tags(_base(repository), plugin)
            if tags:
                followed.append(plugin)
                if (release := self._newest_tag(_base(repository), tags, installed.get(plugin))) is not None:
                    newer[plugin] = release
        return newer, sorted(followed)

    def _indexed(
        self, indexed: dict[str, Requirement], installed: dict[str, str], timeout: float
    ) -> dict[str, PluginRelease]:
        """Each index plugin whose latest stable release is newer than the installed one and allowed by its pin."""
        newer: dict[str, PluginRelease] = {}
        for name, requirement in indexed.items():
            current = installed.get(name)
            latest = self._latest(name, timeout)
            if current is None or latest is None or not requirement.specifier.contains(latest.version):
                continue
            if _newer(latest.version, current):
                newer[name] = latest
        return newer

    def _newest_tag(self, base: str, tags: dict[Version, dict[str, Any]], current: str | None) -> PluginRelease | None:
        """The newest tagged release when it is newer than `current`, with its commit."""
        version = max(tags)
        if current is None or not _newer(str(version), current):
            return None
        return self._tagged(base, str(version), tags[version])

    def _tags(self, base: str, name: str) -> dict[Version, dict[str, Any]]:
        """The stable release tags `<name>/vX.Y.Z` of a repository, by version, with the git object each names."""
        listing: object = self._get()(f"{base}/git/matching-refs/tags/{urllib.parse.quote(name)}/v")
        if not isinstance(listing, list):
            return {}
        prefix = f"refs/tags/{name}/v"
        tags: dict[Version, dict[str, Any]] = {}
        for entry in cast("list[object]", listing)[:MAX_TAGS]:
            item = cast("dict[str, Any]", entry) if isinstance(entry, dict) else {}
            ref, target = item.get("ref"), item.get("object")
            if not isinstance(ref, str) or not ref.startswith(prefix) or not isinstance(target, dict):
                continue
            text = ref.removeprefix(prefix)
            try:
                version = Version(text)
            except InvalidVersion:
                continue
            if str(version) == text and not (version.is_prerelease or version.is_devrelease or version.local):
                tags[version] = cast("dict[str, Any]", target)
        return tags

    def _target(self, base: str, target: dict[str, Any]) -> tuple[str, datetime | None] | None:
        """The commit a tag's git object names, and when an annotated tag was made; None for anything else."""
        sha, kind, made = target.get("sha"), target.get("type"), None
        if kind == "tag" and isinstance(sha, str):
            tag = cast("dict[str, Any]", self._get()(f"{base}/git/tags/{sha}"))
            inner = cast("dict[str, Any]", tag.get("object") or {})
            sha, kind = inner.get("sha"), inner.get("type")
            made = _when(cast("dict[str, Any]", tag.get("tagger") or {}).get("date"))
        if kind != "commit" or not isinstance(sha, str) or not COMMIT.match(sha):
            return None
        return sha, made

    def _tagged(self, base: str, version: str, target: dict[str, Any]) -> PluginRelease | None:
        """A tagged release with its commit; as old as the later of its commit and, for an annotated tag, the tag."""
        named = self._target(base, target)
        if named is None:
            return None
        sha, made = named
        commit = cast("dict[str, Any]", self._get()(f"{base}/commits/{sha}"))
        committed = _when(
            cast("dict[str, Any]", cast("dict[str, Any]", commit.get("commit") or {}).get("committer") or {}).get(
                "date"
            )
        )
        when = None if committed is None else max(committed, made) if made is not None else committed
        return PluginRelease(version, when, commit=sha)

    def tag_commit(self, name: str, version: str, timeout: float) -> str | None:
        """The commit the tag `<name>/v<version>` of a plugin pinned to a GitHub commit names now.

        None when the plugin isn't pinned so or the tag is gone (`LookupError`). GitHub is given its own time limit.

        Raises:
            OSError: If GitHub can't be reached.
        """
        del timeout
        for pin in self._installation.plugin_pins():
            repository = _github(pin)
            if repository is None or canonicalize_name(Requirement(pin.requirement).name) != name:
                continue
            base = _base(repository)
            try:
                ref = self._get()(f"{base}/git/ref/tags/{urllib.parse.quote(name)}/v{urllib.parse.quote(version)}")
                named = self._target(base, cast("dict[str, Any]", ref).get("object") or {})
            except LookupError:
                return None
            return None if named is None else named[0]
        return None

    def announceable(self, timeout: float, old_enough: Callable[[PluginRelease], bool]) -> PluginNews:
        """The newer releases that are `old_enough` and can move with what they need; dependencies first.

        A release whose plugin needs another plugin newer moves only with that plugin's own release, in the same
        update; when that one can't move (too new, held, not released), it waits too, and so does a release that an
        installed plugin doesn't allow. One line says so for each. A tagged release must say the version of its tag.

        Raises:
            HeinzelError: If the installed versions can't be read safely (from `Installation`).
        """
        found, followed = self._scan(timeout)
        moving = {name: release for name, release in found.items() if old_enough(release)}
        held: list[str] = []
        needs: dict[str, list[str]] = {}
        for name, release in list(moving.items()):
            lines = self._new_requires(name, release, timeout)
            if lines is None:
                held.append(f"{name} {release.version} waits: its tag names a commit that doesn't say that version.")
                del moving[name]
            else:
                needs[name] = lines
        if not moving:
            return PluginNews({}, held, followed)
        names = [canonicalize_name(Requirement(pin.requirement).name) for pin in self._installation.plugin_pins()]
        installed = self._installation.installed_versions(names)
        requires = {name: self._requires(name) for name in installed}
        order, waiting = closure(
            installed,
            requires,
            {name: release.version for name, release in moving.items()},
            needs,
            {name: release.version for name, release in found.items()},
        )
        return PluginNews({name: moving[name] for name in order}, [*held, *waiting], followed)

    def _new_requires(self, name: str, release: PluginRelease, timeout: float) -> list[str] | None:
        """What a new release needs, from its metadata; None when a tagged commit says another version."""
        if release.commit is None:
            url = f"{PYPI}/pypi/{urllib.parse.quote(name)}/{urllib.parse.quote(release.version)}/json"
            data: object = self._fetch(url, timeout)
            info = cast("dict[str, Any]", data).get("info") if isinstance(data, dict) else None
            lines = cast("dict[str, Any]", info).get("requires_dist") if isinstance(info, dict) else None
            return [str(line) for line in cast("list[object]", lines)] if isinstance(lines, list) else []
        for pin in self._installation.plugin_pins():
            repository = _github(pin)
            if repository is None or canonicalize_name(Requirement(pin.requirement).name) != name:
                continue
            base = _base(repository)
            project = _github_project_at(self._get(), base, release.commit, pin.subdirectory)
            if str(project.get("version") or "") != release.version:
                return None
            dependencies: object = project.get("dependencies") or []
            return [str(line) for line in cast("list[object]", dependencies)] if isinstance(dependencies, list) else []
        return None

    def _latest(self, name: str, timeout: float) -> PluginRelease | None:
        """The highest stable release on PyPI none of whose files is yanked, with its last upload time.

        The last one: a file added today to an older release is as new as today.
        """
        data: object = self._fetch(f"{PYPI}/pypi/{urllib.parse.quote(name)}/json", timeout)
        releases = cast("dict[str, object]", data).get("releases") if isinstance(data, dict) else None
        if not isinstance(releases, dict):
            return None
        best: tuple[Version, datetime | None] | None = None
        for version, files in cast("dict[str, object]", releases).items():
            try:
                parsed = Version(version)
            except InvalidVersion:
                continue
            uploads = _uploads(files)
            if parsed.is_prerelease or parsed.is_devrelease or uploads is None:
                continue
            if best is None or parsed > best[0]:
                best = (parsed, max(uploads, default=None))
        return None if best is None else PluginRelease(str(best[0]), best[1])

    def release(self, name: str, version: str, timeout: float) -> PluginRelease | None:
        """Release `version` of `name` on PyPI with its last upload time; None if it's missing, yanked or has no files.

        Raises:
            OSError: If PyPI can't be reached or answers with an error.
            ValueError: If the answer is too large or not JSON.
        """
        url = f"{PYPI}/pypi/{urllib.parse.quote(name)}/{urllib.parse.quote(version)}/json"
        data: object = self._fetch(url, timeout)
        files = cast("dict[str, object]", data).get("urls") if isinstance(data, dict) else None
        uploads = _uploads(files)
        return None if not uploads else PluginRelease(version, max(uploads))


def _uploads(files: object) -> list[datetime] | None:
    """When each file of a release was uploaded; None for a release with no files or a yanked one."""
    if not isinstance(files, list) or not files:
        return None
    times: list[datetime] = []
    for entry in cast("list[object]", files):
        if not isinstance(entry, dict):
            return None
        item = cast("dict[str, object]", entry)
        if item.get("yanked"):
            return None
        stamp = item.get("upload_time_iso_8601")
        if isinstance(stamp, str):
            try:
                times.append(datetime.fromisoformat(stamp))
            except ValueError:
                continue
    return times


def closure(
    installed: dict[str, str],
    requires: dict[str, list[str]],
    moving: dict[str, str],
    needs: dict[str, list[str]],
    known: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Which of the `moving` plugin releases can move together, dependencies first, and a line for each that waits.

    `installed` and `requires` are the plugins as they are; `moving` maps each candidate to its new version and `needs`
    to what that version requires; `known` holds every newer release found, also those too new to move. A candidate
    waits when it needs another plugin at a version that won't be there, or when a plugin that stays doesn't allow it.
    Only plugins in the graph count: hz itself and other packages are left to uv.
    """
    graph = _Graph(installed, requires, dict(moving), needs, known)
    held: list[str] = []
    while (line := graph.one_waits()) is not None:
        held.append(line)
    order: list[str] = []
    seen: set[str] = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        for dependency in sorted(_edges(needs.get(name, []), set(graph.moving) - {name})):
            visit(dependency)
        order.append(name)

    for name in sorted(graph.moving):
        visit(name)
    return order, held


@dataclass
class _Graph:
    """The installed plugins and the releases that may still move, for `closure`."""

    installed: dict[str, str]
    requires: dict[str, list[str]]
    moving: dict[str, str]
    needs: dict[str, list[str]]
    known: dict[str, str]

    def one_waits(self) -> str | None:
        """Take the first release that can't move out of `moving`, and say why; None when all of them can."""
        plugins = set(self.installed) | set(self.moving)
        for name in sorted(plugins):
            lines = self.needs.get(name, []) if name in self.moving else self.requires.get(name, [])
            for dependency, specifier in sorted(_edges(lines, plugins - {name}).items()):
                version = self.moving.get(dependency) or self.installed.get(dependency)
                if version is None or specifier.contains(version, prereleases=True):
                    continue
                if name in self.moving:
                    why, new = self._why(dependency, specifier), self.moving.pop(name)
                    return f"{name} {new} waits for {dependency}: it needs {dependency}{specifier}, {why}."
                if dependency in self.moving:
                    new = self.moving.pop(dependency)
                    return f"{dependency} {new} waits: {name} {self.installed[name]} needs {dependency}{specifier}."
        return None

    def _why(self, dependency: str, specifier: SpecifierSet) -> str:
        if dependency in self.moving:
            return f"which would move to {self.moving[dependency]}"
        if dependency in self.known and specifier.contains(self.known[dependency], prereleases=True):
            return f"whose update to {self.known[dependency]} can't be installed yet"
        return "and no release of it that does is known yet"


def _edges(lines: Iterable[str], plugins: set[str]) -> dict[str, SpecifierSet]:
    """What `lines` (`Requires-Dist`) require of `plugins`, by name; extras and other platforms left out."""
    edges: dict[str, SpecifierSet] = {}
    for line in lines:
        try:
            requirement = Requirement(line)
            if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
                continue
        except (InvalidRequirement, UndefinedEnvironmentName):
            continue
        name = canonicalize_name(requirement.name)
        if name in plugins:
            edges[name] = edges.get(name, SpecifierSet()) & requirement.specifier
    return edges


def _base(repository: tuple[str, str]) -> str:
    """The GitHub API path of a repository."""
    return f"repos/{urllib.parse.quote(repository[0])}/{urllib.parse.quote(repository[1])}"


def _github(pin: CorePin) -> tuple[str, str] | None:
    """Owner and repository of a plugin pinned to a full commit of a GitHub repository; None for any other pin."""
    if pin.kind != "commit" or pin.git is None or not COMMIT.match(pin.ref or ""):
        return None
    return PluginSource(kind="git", name=None, url=pin.git).github


def _newer(version: str, current: str) -> bool:
    try:
        return Version(version) > Version(current)
    except InvalidVersion:
        return False


def _when(stamp: object) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp) if isinstance(stamp, str) else None
    except ValueError:
        return None


def plugin_releases(installation: Installation, kernel: Kernel) -> PluginReleases:
    """The system module's `PluginReleases`: PyPI for plugins that follow the index, GitHub for tagged ones.

    GitHub is read through the gh CLI when it is installed (your login), else with the plugin settings' token.
    """
    from heinzel.modules.system.plugin._models import PluginSettings  # noqa: PLC0415 - the submodule's settings

    token = kernel.resolve(PluginSettings).github_token if kernel.provides(PluginSettings) else None
    return PyPIPluginReleases(installation, github=github_getter(token))
