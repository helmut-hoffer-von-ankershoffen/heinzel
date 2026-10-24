"""Which sources `hz system plugin add` installs from, and what can be read about one before installing it.

Adding a plugin runs its code with your rights, so `add` takes only sources whose identity can be shown before you
confirm: a package on PyPI named `heinzel-<name>` (ADR 0025) or a git repository over https. Local paths are for
developing a plugin (`hz system plugin develop`, CLI only); ssh, plain http, file and archive URLs are refused, and so
are credentials inside a URL, which would end up in the uv receipt and the plugin history (ADR 0047).
"""

import base64
import json
import re
import shlex
import shutil
import subprocess
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, NoReturn, cast
from urllib.parse import quote, urlsplit

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
from pydantic import SecretStr

from heinzel import HeinzelError, __version__
from heinzel.kernel import CORE_DISTRIBUTION, subprocess_environment
from heinzel.modules.system._github import GITHUB_API, get_json

PREFIX = "heinzel-"
PYPI = "https://pypi.org"
TIMEOUT = 10
"""Seconds to wait for PyPI or GitHub while reading what a plugin is."""
MAX_BYTES = 5_000_000
"""Largest PyPI answer read; a project's JSON with its release list is far smaller."""
SAFE_PART = re.compile(r"^[A-Za-z0-9_+][A-Za-z0-9._/+-]*$")
"""A git ref or subdirectory: no spaces, no shell or URL syntax, no leading `-` or `.`."""
NAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$", re.IGNORECASE)
"""A distribution name as PEP 508 allows it."""
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
"""C0 and C1 control characters, ESC among them: metadata never redraws a terminal."""
ARCHIVES = (".whl", ".zip", ".tar.gz", ".tgz", ".tar.bz2")
PATH_LIKE = (".", "/", "~", "\\")
SCP_GITHUB = re.compile(r"^[A-Za-z0-9._-]+@github\.com:(?P<path>[^:]+)$", re.IGNORECASE)
"""An scp-like ssh remote on GitHub, `git@github.com:owner/repo.git`."""

JsonGetter = Callable[[str], Any]
"""Reads a JSON document: from the GitHub API by path (`repos/o/r`), or from PyPI by URL."""


@dataclass(frozen=True, slots=True)
class PluginSource:
    """A parsed plugin source: a PyPI requirement or an https git repository."""

    kind: Literal["pypi", "git"]
    name: str | None
    """The canonical distribution name, when the source says it."""
    requirement: Requirement | None = None
    """For PyPI: the requirement as given."""
    url: str | None = None
    """For git: the repository's https URL, without `git+`, ref or fragment."""
    ref: str | None = None
    subdirectory: str | None = None

    @property
    def github(self) -> tuple[str, str] | None:
        """Owner and repository, for a repository on github.com."""
        if self.url is None:
            return None
        parts = urlsplit(self.url)
        segments = [s for s in parts.path.split("/") if s]
        if parts.hostname != "github.com" or len(segments) != 2:  # noqa: PLR2004 - owner/repository
            return None
        return segments[0], segments[1].removesuffix(".git")

    def git_requirement(self, name: str | None, revision: str | None) -> str:
        """The requirement uv installs: `name @ git+URL@REV#subdirectory=...`, with what is known of each."""
        ref = revision or self.ref
        fragment = f"#subdirectory={self.subdirectory}" if self.subdirectory else ""
        url = f"git+{self.url}{'@' + ref if ref else ''}{fragment}"
        return f"{name} @ {url}" if name else url


def refused(spec: str, why: str) -> NoReturn:
    """Refuse a source `add` doesn't take, saying what it does take.

    Raises:
        HeinzelError: Always.
    """
    msg = (
        f"Can't add {spec!r}: {why}. `hz system plugin add` takes a first-party name (`hz system plugin add github`), "
        f"a PyPI package named {PREFIX}<name>, or a git URL over https "
        "(`git+https://github.com/acme-example/heinzel-acme@v1.0.0`); a checkout on this machine goes with "
        "`hz system plugin develop PATH`."
    )
    raise HeinzelError(msg)


def parse(spec: str) -> PluginSource:  # noqa: C901 - one rule per refusal reads best flat
    """Parse what `add` was given into a PyPI requirement or an https git repository.

    Raises:
        HeinzelError: If the source is anything else, or a malformed one.
    """
    text = spec.strip()
    if not text:
        refused(spec, "it is empty")
    if text.startswith("-") or any(c in text for c in "\0\r\n\t"):
        refused(spec, "it looks like an option or holds control characters")
    if text.startswith(PATH_LIKE) or text.startswith("file:") or (len(text) > 1 and text[1] == ":"):
        refused(spec, "local paths aren't added, they are developed")
    if text.startswith("https://") and " " not in text:
        text = f"git+{text}"
    if text.startswith("git+"):
        return _git(spec, None, text)
    try:
        requirement = Requirement(text)
    except InvalidRequirement:
        requirement = None
    if requirement is None:
        if "://" in text or text.startswith(("git@", "ssh:")):
            refused(spec, "only https URLs are accepted")
        refused(spec, "it is no package name, requirement or URL")
    name = canonicalize_name(requirement.name)
    if requirement.url is not None:
        if not requirement.url.startswith("git+"):
            refused(spec, "only git repositories over https are accepted, not archives or other URLs")
        return _git(spec, name, requirement.url)
    if name == CORE_DISTRIBUTION:
        refused(spec, "Heinzelmännchen itself is updated with `hz system update`")
    if not name.startswith(PREFIX):
        refused(spec, f"plugins on PyPI are named {PREFIX}<name> (ADR 0025)")
    return PluginSource(kind="pypi", name=name, requirement=requirement)


def _git(spec: str, name: str | None, url: str) -> PluginSource:  # noqa: C901 - one rule per refusal reads best flat
    """An https git repository from `git+https://host/path[@ref][#subdirectory=dir]`.

    Raises:
        HeinzelError: If it isn't https, carries credentials, a query or an unsafe ref or subdirectory.
    """
    parts = urlsplit(url.removeprefix("git+"))
    if parts.scheme != "https":
        refused(spec, f"only git over https is accepted, not {parts.scheme or 'no scheme'}")
    if "@" in parts.netloc or not parts.hostname:
        refused(spec, "a URL must not carry a user name or token; private GitHub repositories use your gh login")
    if parts.query:
        refused(spec, "a git URL takes no query; a ref goes after @, a subdirectory in #subdirectory=")
    if parts.path.lower().endswith(ARCHIVES):
        refused(spec, "archives aren't accepted, only git repositories")
    path, ref = parts.path, None
    if "@" in path:
        path, ref = path.rsplit("@", 1)
    subdirectory = None
    if parts.fragment:
        key, _, value = parts.fragment.partition("=")
        if key != "subdirectory" or not value:
            refused(spec, "the only fragment a git URL takes is #subdirectory=<dir>")
        subdirectory = value
    for part in (ref, subdirectory):
        if part is not None and (not SAFE_PART.match(part) or ".." in part.split("/") or part.endswith(("/", ".lock"))):
            refused(spec, f"{part!r} is no git ref or subdirectory")
    if not path.strip("/"):
        refused(spec, "the URL names no repository")
    if "%" in path or any(segment in {".", ".."} for segment in path.split("/")):
        refused(spec, "the repository path has dot segments or escapes; give it as the repository shows it")
    netloc = parts.netloc.lower()
    return PluginSource(
        kind="git", name=name, url=f"https://{netloc}{path.rstrip('/')}", ref=ref, subdirectory=subdirectory
    )


@dataclass(frozen=True, slots=True)
class Metadata:
    """What a plugin says about itself, read before it is installed."""

    name: str | None = None
    version: str | None = None
    summary: str = ""
    publisher: str | None = None
    private: bool | None = None
    revision: str | None = None
    page: str | None = None


def from_pypi(source: PluginSource, get: JsonGetter) -> Metadata:
    """The release a PyPI requirement resolves to (the newest matching its specifier), its summary and author.

    Raises:
        HeinzelError: If PyPI has no such project, or no release matches the specifier.
        HTTPError: If PyPI answers with another error.
        OSError: If PyPI can't be reached.
        ValueError: If the answer isn't the JSON PyPI sends.
    """
    if source.name is None or source.requirement is None:  # pragma: no cover - parse() sets both for PyPI
        msg = "a PyPI source has a name"
        raise ValueError(msg)
    try:
        data = get(f"{PYPI}/pypi/{quote(source.name)}/json")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # noqa: PLR2004 - not found
            exc.close()
            msg = f"PyPI has no project {source.name!r}; check the name with `hz system plugin search`."
            raise HeinzelError(msg) from None
        raise
    info = cast("dict[str, Any]", data["info"])
    releases = cast("dict[str, list[dict[str, Any]]]", data.get("releases") or {})
    available = [v for v, files in releases.items() if files and not all(f.get("yanked") for f in files)]
    matching = sorted(source.requirement.specifier.filter(available), key=_version_key)
    if source.requirement.specifier and not matching:
        msg = f"No release of {source.name} on PyPI matches {source.requirement.specifier}."
        raise HeinzelError(msg)
    latest = str(info.get("version") or "")
    version = matching[-1] if matching else (latest if _version_key(latest)[0] else None)
    author = info.get("author") or info.get("maintainer") or _name_of(info.get("author_email"))
    return Metadata(
        name=source.name,
        version=version,
        summary=clean(info.get("summary")),
        publisher=clean(author) or None,
        page=f"{PYPI}/project/{source.name}/",
    )


def from_github(source: PluginSource, get: JsonGetter) -> Metadata:
    """A GitHub repository's owner, visibility, the commit its ref points at, and its pyproject's name and version.

    Raises:
        HeinzelError: If the repository, the ref or the pyproject can't be found.
        OSError: If GitHub can't be reached.
        ValueError: If an answer isn't the JSON GitHub sends.
    """
    owner, repository = source.github or ("", "")
    base = f"repos/{quote(owner)}/{quote(repository)}"
    try:
        repo = cast("dict[str, Any]", get(base))
        ref = source.ref or str(repo["default_branch"])
        revision = str(cast("dict[str, Any]", get(f"{base}/commits/{quote(ref, safe='')}"))["sha"])
        where = f"{source.subdirectory}/pyproject.toml" if source.subdirectory else "pyproject.toml"
        content = cast("dict[str, Any]", get(f"{base}/contents/{quote(where)}?ref={revision}"))
    except LookupError as exc:  # a missing repository, ref or file: gh and urllib both end up here
        msg = (
            f"Can't read {source.url}{'@' + source.ref if source.ref else ''} on GitHub ({exc}). Check the URL; for a "
            "private repository, sign in with `gh auth login`."
        )
        raise HeinzelError(msg) from exc
    project = cast("dict[str, Any]", tomllib.loads(base64.b64decode(str(content["content"])).decode())["project"])
    authors = cast("list[dict[str, Any]]", project.get("authors") or [])
    owner_info = cast("dict[str, Any]", repo.get("owner") or {})
    publisher = clean(owner_info.get("login") or owner)
    if authors and authors[0].get("name"):
        publisher = f"{publisher} ({clean(authors[0]['name'])})"
    version = clean(project.get("version"))
    return Metadata(
        name=checked_name(str(project["name"]), str(source.url)),
        version=version if _version_key(version)[0] else None,
        summary=clean(project.get("description") or repo.get("description")),
        publisher=publisher,
        private=bool(repo.get("private")),
        revision=revision,
        page=str(repo.get("html_url") or source.url),
    )


def github_getter(token: SecretStr | None) -> JsonGetter:
    """Reads the GitHub API through the gh CLI when it is installed, so private repositories work with your login.

    Without gh, it falls back to an https GET with `token` (public repositories only, ADR 0033). Either way a missing
    repository, ref or file raises `LookupError`.
    """
    gh = shutil.which("gh")

    def through_gh(path: str) -> Any:  # noqa: ANN401 - free-form JSON
        try:
            result = subprocess.run(  # noqa: S603 - resolved gh, fixed arguments, no shell
                [gh or "gh", "api", "-H", "Accept: application/vnd.github+json", path],
                capture_output=True,
                text=True,
                check=False,
                timeout=TIMEOUT,
                env=subprocess_environment(),
            )
        except subprocess.TimeoutExpired as exc:
            msg = f"gh api {path} timed out"
            raise OSError(msg) from exc
        if result.returncode != 0:
            problem = result.stderr.strip() or f"gh api {path} failed"
            if "404" in problem or "Not Found" in problem:
                raise LookupError(problem)
            raise OSError(problem)
        return json.loads(result.stdout)

    def through_https(path: str) -> Any:  # noqa: ANN401 - free-form JSON
        try:
            return get_json(f"{GITHUB_API}/{path}", token, TIMEOUT)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # noqa: PLR2004 - not found
                exc.close()
                msg = f"not found: {path}"
                raise LookupError(msg) from None
            raise

    return through_gh if gh else through_https


def pypi_get(url: str) -> Any:  # noqa: ANN401 - free-form JSON
    """GET a PyPI JSON URL with a `heinzel/<version>` User-Agent.

    Raises:
        OSError: If PyPI can't be reached or answers with an error (an `HTTPError` for 404).
        ValueError: If the URL is not PyPI's, or the answer is too large or not JSON.
    """
    if not url.startswith(f"{PYPI}/pypi/"):
        msg = f"not a PyPI JSON URL: {url}"
        raise ValueError(msg)
    request = urllib.request.Request(url, headers={"User-Agent": f"heinzel/{__version__}"})  # noqa: S310 - https, checked
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - https, checked above
        body: bytes = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        msg = f"the answer is larger than {MAX_BYTES} bytes"
        raise ValueError(msg)
    return json.loads(body)


def git_credentials(environment: dict[str, str]) -> dict[str, str]:
    """Variables that let git (run by uv) ask the gh CLI for github.com credentials; none without gh.

    So `uv tool install` can fetch a private repository with your gh login without the token ever passing through
    Heinzelmännchen: git asks `gh auth git-credential` itself. Appended after any `GIT_CONFIG_*` already set.
    """
    gh = shutil.which("gh")
    if gh is None:
        return {}
    try:
        count = int(environment.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        count = 0
    quoted = shlex.quote(gh)  # git runs a `!` helper through the shell
    return {
        "GIT_CONFIG_COUNT": str(count + 1),
        f"GIT_CONFIG_KEY_{count}": "credential.https://github.com.helper",
        f"GIT_CONFIG_VALUE_{count}": f"!{quoted} auth git-credential",
    }


def clean(text: object) -> str:
    """Metadata text as it may be shown: control characters dropped, outer whitespace trimmed; empty for none."""
    return CONTROL.sub("", str(text)).strip() if text else ""


def checked_name(name: str, where: str) -> str:
    """A plugin's canonical name as its own metadata gives it, refused when it can't name a plugin.

    Raises:
        HeinzelError: If it isn't a valid distribution name, or claims to be Heinzelmännchen itself.
    """
    if not NAME.match(name):
        msg = f"{where} names its distribution {clean(name)!r}, which is no valid name; it isn't added."
        raise HeinzelError(msg)
    canonical = canonicalize_name(name)
    if canonical == CORE_DISTRIBUTION:
        msg = f"{where} calls itself {CORE_DISTRIBUTION}; Heinzelmännchen is updated with `hz system update`."
        raise HeinzelError(msg)
    return canonical


def browser_url(url: str | None, ref: str | None = None, subdirectory: str | None = None) -> str | None:
    """A repository URL a browser opens: https only, without credentials or `.git`; None for anything else.

    A plugin in a subdirectory gets `/tree/<ref>/<dir>`, the form GitHub and GitLab use.
    """
    if not url:
        return None
    parts = urlsplit(url.removeprefix("git+"))
    if parts.scheme != "https" or not parts.hostname:
        return None
    host = parts.hostname + (f":{parts.port}" if parts.port else "")  # drops `user:token@`
    base = f"https://{host}{parts.path.rstrip('/').removesuffix('.git')}"
    if subdirectory:
        return f"{base}/tree/{ref or 'HEAD'}/{subdirectory.strip('/')}"
    return base


def installed_repository_url(url: str | None, ref: str | None = None, subdirectory: str | None = None) -> str | None:
    """Where a browser shows the repository a plugin was installed from, per its `direct_url.json` (PEP 610).

    `url` is the `url` beside its `vcs_info`: `git+` and credentials are dropped, and a GitHub remote over ssh
    (`ssh://git@github.com/o/r.git`, `git@github.com:o/r.git`) becomes its https page. A GitHub repository gets
    `/tree/<ref>` (and `/<subdirectory>` for a plugin in one), so the link opens the code that is installed; another
    host gets what `browser_url` makes of it. None when it can't be shown over https.
    """
    if not url:
        return None
    text = url.strip().removeprefix("git+")
    if match := SCP_GITHUB.match(text):
        text = f"https://github.com/{match['path'].lstrip('/')}"
    parts = urlsplit(text)
    if parts.scheme == "ssh" and parts.hostname == "github.com":
        text = f"https://github.com{parts.path}"
        parts = urlsplit(text)
    base = browser_url(text)
    if base is None or parts.hostname != "github.com" or not ref:
        return browser_url(text, ref, subdirectory)
    return f"{base}/tree/{ref}" + (f"/{subdirectory.strip('/')}" if subdirectory else "")


def repository_url(spec: str) -> str | None:
    """The repository an install spec names, as `browser_url` shows it; None when it names none over https."""
    url = spec.split(" @ ", 1)[-1].strip()
    if not url.startswith(("git+https://", "https://")):
        return None
    parts = urlsplit(url.removeprefix("git+"))
    path, ref = parts.path, None
    if "@" in path:
        path, ref = path.rsplit("@", 1)
    key, _, subdirectory = parts.fragment.partition("=")
    return browser_url(
        f"https://{parts.netloc}{path}", ref, subdirectory if key == "subdirectory" and subdirectory else None
    )


def _version_key(version: str) -> tuple[int, Version | str]:
    try:
        return (1, Version(version))
    except InvalidVersion:
        return (0, version)


def _name_of(email: object) -> str | None:
    """`Ada <ada@example.com>` → `Ada`; the address alone when there is no name."""
    if not email:
        return None
    text = str(email).split(",", 1)[0].strip()
    return text.split("<", 1)[0].strip().strip('"') or text
