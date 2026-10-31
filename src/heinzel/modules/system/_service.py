"""Service of the system module."""

import json
import platform
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date
from typing import Any, cast, override
from urllib.parse import urlsplit

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, SecretStr

from heinzel import InfoLet, Kernel, __version__
from heinzel.kernel import EDGE_WHEEL_PREFIX, EdgeRelease, Installation, ReleaseCache, ReleaseSource
from heinzel.modules.system import _github
from heinzel.modules.system._models import ModuleInfo, ReleaseCheck, ReleaseNotes, SystemInfo

DEFAULT_REPOSITORY = "helmut-hoffer-von-ankershoffen/heinzel"
"""Where releases come from when the installation doesn't say, e.g. in a development checkout."""
RELEASE_TIMEOUT = 5
"""Seconds to wait for the package source, when someone asked, before giving up with a warning."""
_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")
PAGE_SIZE = 30
"""Releases asked for per page by `hz system changelog --newer`."""
MAX_PAGES = 4
"""Pages asked for at most: the newest `PAGE_SIZE * MAX_PAGES` releases, in a bounded number of requests."""
MAX_NOTES = 20_000
"""Characters of one release's notes kept at most; longer notes end with a pointer to the release page."""
CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029]")
"""Control characters but tab and line feed, and the Unicode line and paragraph separators."""
ZERO_WIDTH_JOINER = "\u200d"
"""The one invisible format character kept: emoji are built with it."""
VARIATION_SELECTORS = re.compile(r"[\U000e0100-\U000e01ef]|(?<=[\ufe00-\ufe0f])[\ufe00-\ufe0f]+|^[\ufe00-\ufe0f]+")
"""Supplementary variation selectors, and the basic ones but the first after a character (emoji need U+FE0F): a run of
them after one visible character can carry a payload a reader can't see."""
_TAG = re.compile(r"[A-Za-z0-9._+-]+")


def clean(text: str) -> str:
    """Third-party text without control or invisible format characters, with line feeds for line breaks.

    Format characters (Unicode category Cf) are bidirectional controls, zero-width characters and tag characters: they
    make text show something other than it says, or hide text a reader can't see but a model reads. So can a run of
    variation selectors, which keeps at most one.
    """
    text = CONTROLS.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = "".join(c for c in text if c == ZERO_WIDTH_JOINER or unicodedata.category(c) != "Cf")
    return VARIATION_SELECTORS.sub("", text)


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


def _github_link(url: object) -> str | None:
    """`url` if it links to github.com; anything else in an answer stays out of the terminal."""
    return url if isinstance(url, str) and url.startswith("https://github.com/") else None


EDGE_POINTER = f"https://github.com/{DEFAULT_REPOSITORY}/releases/download/edge-channel/edge.json"
MAX_POINTER = 4096
_SHA256 = re.compile(r"[0-9a-f]{64}")


class GitHubReleases(ReleaseSource):
    """The releases of the GitHub repository Heinzelmännchen was installed from: its `ReleaseSource` (ADR 0031).

    The release check (`latest`) never sends a token: unauthenticated GETs with only the `heinzel/<version>`
    User-Agent. `newer_than`, asked for by someone, sends their GitHub token if given one, to api.github.com only.
    """

    def __init__(self, repository: str) -> None:
        """Ask the releases of `repository` (`owner/name`)."""
        self.repository = repository

    @property
    def latest_url(self) -> str:
        """Where the latest release is asked for."""
        return f"{_github.GITHUB_API}/repos/{self.repository}/releases/latest"

    def latest(self, timeout: float) -> tuple[str, str | None]:
        """The version of the latest release and its page on github.com.

        Raises:
            OSError: If GitHub can't be reached or answers with an error, e.g. when rate-limited.
            ValueError: If the answer is no release (`InvalidVersion` is one).
            KeyError: If the answer lacks a field.
            TypeError: If the answer is no JSON object.
        """
        answer = _github.get_json(self.latest_url, None, timeout)
        return str(Version(str(answer["tag_name"]).removeprefix("v"))), _github_link(answer["html_url"])

    @override
    def latest_version(self, timeout: float) -> str:
        """The version of the latest release, for the release cache."""
        return self.latest(timeout)[0]

    @override
    def highest_version(self, timeout: float, *, prereleases: bool = False) -> str:
        """The highest version among the newest `PAGE_SIZE` releases, in SemVer order rather than by date.

        GitHub's latest release is the one published last, so a fix for an older line published after a newer release
        would be "latest"; `hz system update` must never move back to it. One unauthenticated GET, like the release
        check (ADR 0031).

        Raises:
            OSError: If GitHub can't be reached or answers with an error, e.g. when rate-limited.
            ValueError: If the answer is too large or not JSON, or names no published release.
            TypeError: If the answer is no list.
        """
        answer: object = _github.get_json(self.releases_url(1), None, timeout)
        if not isinstance(answer, list):
            msg = "the answer is no list of releases"
            raise TypeError(msg)
        versions = [
            version
            for item in cast("list[object]", answer)
            if (version := _published_version(item, prereleases=prereleases)) is not None
        ]
        if not versions:
            msg = f"{self.repository} has no published release"
            raise ValueError(msg)
        return str(max(versions))

    @override
    def edge_release(self, timeout: float) -> EdgeRelease:
        """The newest edge release from the channel pointer: a release asset, so it needs no API quota (ADR 0114).

        Raises:
            OSError: If GitHub can't be reached or answers with an error.
            TypeError: If the pointer is no JSON object.
            ValueError: If the pointer is too large, not JSON or names anything but an edge release.
        """
        request = urllib.request.Request(EDGE_POINTER, headers={"User-Agent": f"heinzel/{__version__}"})
        body = _github._open(request, timeout)  # noqa: SLF001 - the one seam that reads from GitHub
        if len(body) > MAX_POINTER:
            msg = "the edge pointer is too large"
            raise ValueError(msg)
        return _edge_release(json.loads(body))

    def releases_url(self, page: int) -> str:
        """Where a page of releases, newest first, is asked for."""
        return f"{_github.GITHUB_API}/repos/{self.repository}/releases?per_page={PAGE_SIZE}&page={page}"

    def newer_than(self, version: str, timeout: float, token: SecretStr | None = None) -> list[ReleaseNotes]:
        """The notes of every published release newer than `version`, newest first; drafts and pre-releases skipped.

        At most `MAX_PAGES` requests, stopping at the first page that is short or reaches `version`. Notes are
        third-party text: cleaned of control and bidirectional characters, and cut at `MAX_NOTES` characters. A
        `token` raises GitHub's rate limit; without one the requests are unauthenticated.

        Raises:
            OSError: If GitHub can't be reached or answers with an error, e.g. when rate-limited.
            ValueError: If an answer is too large or not JSON.
            TypeError: If an answer is no list.
        """
        installed, found = Version(version), dict[Version, ReleaseNotes]()
        for page in range(1, MAX_PAGES + 1):
            answer: object = _github.get_json(self.releases_url(page), token, timeout)
            if not isinstance(answer, list):
                msg = "the answer is no list of releases"
                raise TypeError(msg)
            items = cast("list[object]", answer)
            reached = False
            for notes in filter(None, (_release_notes(item, self.repository) for item in items)):
                if Version(notes.version) > installed:
                    found.setdefault(Version(notes.version), notes)
                else:
                    reached = True
            if reached or len(items) < PAGE_SIZE:
                break
        return [found[key] for key in sorted(found, reverse=True)]


def _edge_release(answer: object) -> EdgeRelease:
    """The channel pointer's facts, each checked; the digest is what the installer then verifies against.

    Raises:
        TypeError: If the pointer is no JSON object.
        ValueError: If the pointer doesn't name one edge release's wheel on the official repository.
    """
    if not isinstance(answer, dict):
        msg = "the edge pointer is no JSON object"
        raise TypeError(msg)
    pointer = cast("dict[str, object]", answer)
    version, wheel, digest = pointer.get("version"), pointer.get("wheel"), pointer.get("sha256")
    if not (isinstance(version, str) and isinstance(wheel, str) and isinstance(digest, str)):
        msg = "the edge pointer lacks its version, wheel or digest"
        raise TypeError(msg)
    parsed = Version(version)
    expected = f"{EDGE_WHEEL_PREFIX}{version}/heinzel-{version}-py3-none-any.whl"
    if str(parsed) != version or not parsed.is_devrelease or wheel != expected or not _SHA256.fullmatch(digest):
        msg = "the edge pointer does not name an edge release"
        raise ValueError(msg)
    return EdgeRelease(version, wheel, digest)


def _published_version(item: object, *, prereleases: bool) -> Version | None:
    """A published release's version from GitHub's answer; None for drafts, unwanted pre-releases and non-releases."""
    if not isinstance(item, dict):
        return None
    release = cast("dict[str, object]", item)
    if release.get("draft"):
        return None
    try:
        version = Version(clean(str(release.get("tag_name", ""))).removeprefix("v"))
    except InvalidVersion:
        return None
    if (release.get("prerelease") or version.is_prerelease) and not prereleases:
        return None
    if release.get("prerelease") and not version.is_prerelease:
        return None  # a final version still held for acceptance (ADR 0114): not released yet, whoever asks
    return version


def _release_page(url: object, repository: str) -> str | None:
    """`url` if it is a release page of `repository` on github.com, character by character; None otherwise."""
    prefix = f"https://github.com/{repository}/releases/tag/"
    if not isinstance(url, str) or not url.isascii():
        return None
    if url.lower().startswith(prefix.lower()) and _TAG.fullmatch(url[len(prefix) :]):
        return url
    return None


def _cut(notes: str, url: str | None) -> str:
    """`notes` cut at `MAX_NOTES` characters, closing a code block left open, with a pointer to all of it."""
    cut = notes[:MAX_NOTES].rstrip()
    if cut.count("```") % 2:
        cut += "\n```"
    return f"{cut}\n\n... (cut short; read all of it on {url or 'the release page'})"


def _release_notes(item: object, repository: str) -> ReleaseNotes | None:
    """A published release from GitHub's answer; None for drafts, pre-releases and anything that is no release."""
    if not isinstance(item, dict):
        return None
    release = cast("dict[str, object]", item)
    if release.get("draft") or release.get("prerelease"):
        return None
    try:
        version = Version(clean(str(release.get("tag_name", ""))).removeprefix("v"))
    except InvalidVersion:
        return None
    if version.is_prerelease:
        return None
    published = release.get("published_at")
    try:
        day = date.fromisoformat(published[:10]) if isinstance(published, str) else None
    except ValueError:
        day = None
    url = _release_page(release.get("html_url"), repository)
    body = release.get("body")
    notes = clean(body).strip() if isinstance(body, str) else ""
    if len(notes) > MAX_NOTES:
        notes = _cut(notes, url)
    return ReleaseNotes(version=str(version), date=day, notes=notes, url=url)


def github_releases(installation: Installation) -> GitHubReleases:
    """The releases of the repository in the uv tool receipt, or of the default repository."""
    requirements = installation.requirements()
    return GitHubReleases(repository_of(requirements[0] if requirements else None))


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
                    repository=record.origin.url,
                    lets=dict(lets),
                )
            )
        return infos

    def info(self) -> SystemInfo:
        """Versions, interpreter, settings, modules and the sections modules contribute."""
        return SystemInfo(
            version=__version__,
            repository=next((r.origin.url for r in self._kernel.modules if r.origin.builtin), None),
            python_version=platform.python_version(),
            python_executable=sys.executable,
            platform=platform.platform(),
            settings=self._kernel.settings.model_dump(mode="json"),
            modules=self.modules(),
            plugin_errors=dict(self._kernel.errors),
            sections={
                record.qualname: section
                for record, let in self._kernel.lets(InfoLet)
                if (section := self._section(let)) not in ({}, [], None)  # nothing to say, no empty heading
            },
            latest_release=ReleaseCache(self._kernel.settings.cache_dir).latest,
        )

    def release(self, requirement: str | None) -> ReleaseCheck:
        """Ask the package source for the latest release and cache it; offline or rate-limited is a warning.

        Never sends a token (ADR 0031): one unauthenticated GET with the `heinzel/<version>` User-Agent.

        Args:
            requirement: The requirement Heinzelmännchen was installed from, as in the uv tool receipt.
        """
        releases = GitHubReleases(repository_of(requirement))
        source, cache = releases.latest_url, ReleaseCache(self._kernel.settings.cache_dir)
        try:
            latest, url = releases.latest(RELEASE_TIMEOUT)
        # Isolation boundary: an unreachable, rate-limited or odd package source is a warning, never a crash.
        except (OSError, ValueError, KeyError, TypeError) as exc:  # InvalidVersion is a ValueError
            # An HTTPError reads best whole (it carries the status, e.g. 403 when rate-limited); others by reason.
            reason: object = exc
            if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError):
                reason = exc.reason
            cache.stamp(error=str(reason))  # the hourly check waits too: nobody gains from asking again right away
            warning = f"Could not ask {source} for the latest release ({reason})."
            return ReleaseCheck(
                current=__version__, latest=None, update_available=False, url=None, source=source, warnings=[warning]
            )
        cache.remember(latest)
        return ReleaseCheck(
            current=__version__,
            latest=latest,
            update_available=Version(latest) > Version(__version__),
            url=url,
            source=source,
            warnings=[],
        )

    def _section(self, let: InfoLet) -> Any:  # noqa: ANN401 - sections are free-form JSON
        try:
            section = self._kernel.call(let.provider)
        except Exception as exc:  # noqa: BLE001 - a broken module must not break `hz system info`
            return {"error": f"{type(exc).__name__}: {exc}"}
        return section.model_dump(mode="json") if isinstance(section, BaseModel) else section
