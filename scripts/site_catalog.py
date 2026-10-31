# /// script
# requires-python = ">=3.12"
# dependencies = ["packaging==26.3"]
# ///
"""Read public plugin metadata at immutable revisions; never import or install a provider.

The checked-in snapshot supports offline documentation builds. Publication refreshes another snapshot with
`uv run --script scripts/site_catalog.py --output /tmp/catalog.json`; GitHub CLI handles authentication
without exposing it.
"""

import argparse
import base64
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import tempfile
import tomllib
import unicodedata
import urllib.request
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from packaging.requirements import Requirement
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import Version

type Object = dict[str, Any]  # Untrusted JSON/TOML is narrowed by the bounded readers below.
ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "docs" / "_data" / "catalog.json"
REPOSITORY = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+")
REVISION = re.compile(r"[a-f0-9]{40}")
NAME = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")
VIDEO = re.compile(r"[A-Za-z0-9_-]{11}")
COLOR = re.compile(r"#[a-fA-F0-9]{6}")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
CATEGORIES = ("utilities", "connectors", "themes", "reference")
MAX_BLOB = 96 * 1024
MAX_RESPONSE = 12 * 1024 * 1024
MAX_TREE = 12000
MAX_SCREENSHOTS = 8
MAX_ARTWORK = 2 * 1024 * 1024
MAX_REPOSITORIES = 50
MAX_PROJECTS = 500
MAX_SOURCE_PROJECTS = 32
MAX_SOURCE_BLOBS = 64
MAX_API_REQUESTS = 240
APP_PROFILE_FORMAT = 2
MIN_PROFILE_ARTIFACTS = 2
MAX_PROFILE_ARTIFACTS = 64
MAX_ARTIFACT_VERSION = 80
MAX_ARTIFACT_FILENAME = 240


def text(value: object, limit: int = 500) -> str:
    """Accept bounded text without invisible control characters.

    Raises:
        ValueError: If the source is not suitable public display text.
    """
    if (
        not isinstance(value, str)
        or len(value) > limit
        or any(unicodedata.category(c) in {"Cc", "Cf"} and c not in "\n\t" for c in value)
    ):
        msg = "Catalog text must be bounded and contain no control characters"
        raise ValueError(msg)
    return value


def relative(value: object) -> str:
    """Accept a repository-relative regular-file path, never an URL or traversal.

    Raises:
        ValueError: If a path could escape its owning project.
    """
    result = text(value, 240)
    path = PurePosixPath(result)
    if not result or path.is_absolute() or any(p in {".", ".."} for p in result.split("/")):
        msg = "Catalog asset paths must stay inside their project"
        raise ValueError(msg)
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", result):
        msg = "Catalog paths contain unsupported characters"
        raise ValueError(msg)
    return result


def object_data(value: object) -> Object:
    """Return a JSON object, or an empty object for an absent optional table."""
    return value if isinstance(value, dict) else {}


def strings(value: object, limit: int = 8) -> list[str]:
    """Read a bounded optional list of display strings.

    Raises:
        ValueError: If a declaration exceeds the list limit.
    """
    if not isinstance(value, list):
        return []
    if len(value) > limit:
        msg = "Too many catalog declarations"
        raise ValueError(msg)
    return [text(item, 180) for item in value]


class GitHub:
    """A read-only public GitHub metadata client; authentication stays inside gh."""

    def __init__(self) -> None:
        """Find the standard GitHub CLI.

        Raises:
            ValueError: If refreshing is unavailable; offline site builds still work.
        """
        executable = shutil.which("gh")
        if executable is None:
            msg = "Install GitHub CLI to refresh the public catalog; offline builds use the checked-in snapshot"
            raise ValueError(msg)
        self.executable = executable
        self.requests = 0

    def request(self, path: str) -> object:
        """Read one bounded API response, never an arbitrary author-provided URL.

        Raises:
            ValueError: If GitHub refuses or truncates the metadata.
        """
        if self.requests >= MAX_API_REQUESTS:
            msg = "Public catalog API budget exhausted; optional sources are omitted"
            raise ValueError(msg)
        self.requests += 1
        result = subprocess.run(  # noqa: S603 - gh is resolved; paths are constructed from validated metadata
            [self.executable, "api", "--hostname", "github.com", path],
            capture_output=True,
            check=False,
            timeout=45,
        )
        if result.returncode or len(result.stdout) > MAX_RESPONSE:
            msg = "Public catalog refresh could not read GitHub metadata; keeping the previously deployed site"
            raise ValueError(msg)
        value: object = json.loads(result.stdout)
        return value

    def get(self, path: str) -> Object:
        """Read a JSON object from GitHub."""
        return object_data(self.request(path))

    def listing(self, path: str) -> list[Object]:
        """Read a bounded JSON array from GitHub.

        Raises:
            TypeError: If the service did not return a list.
        """
        values = self.request(path)
        if not isinstance(values, list):
            msg = "Expected a public GitHub listing"
            raise TypeError(msg)
        return [object_data(value) for value in values]

    def blob(self, repo: str, entry: Object) -> bytes:
        """Read a regular, bounded Git blob from the checked public source.

        Raises:
            ValueError: If the tree entry is linked, oversized or malformed.
        """
        if entry.get("type") != "blob" or entry.get("mode") not in {"100644", "100755"}:
            msg = "Catalog metadata cannot follow symlinks or submodules"
            raise ValueError(msg)
        sha = text(entry.get("sha"), 40)
        if not REVISION.fullmatch(sha) or entry.get("size", MAX_BLOB + 1) > MAX_BLOB:
            msg = "Catalog metadata blob is oversized or has no immutable identity"
            raise ValueError(msg)
        value = self.get(f"repos/{repo}/git/blobs/{sha}")
        content = base64.b64decode(text(value.get("content"), MAX_BLOB * 2), validate=False)
        if len(content) > MAX_BLOB:
            msg = "Catalog metadata exceeds its size limit"
            raise ValueError(msg)
        return content


class SourceBlobs:
    """Limit the metadata work any one public topic source can cause."""

    def __init__(self, api: GitHub) -> None:
        """Start a fresh per-source read budget."""
        self.api = api
        self.reads = 0

    def blob(self, repo: str, entry: Object) -> bytes:
        """Read at most the source's share of metadata blobs.

        Raises:
            ValueError: If the source would exceed its budget.
        """
        if self.reads >= MAX_SOURCE_BLOBS:
            msg = "Catalog source exceeds its metadata read budget"
            raise ValueError(msg)
        self.reads += 1
        return self.api.blob(repo, entry)


def theme_data(source: Object, path: str, data: Object) -> Object | None:
    """Project declared theme data into an inert palette preview, without executing its module."""
    light, dark = object_data(data.get("light")), object_data(data.get("dark"))
    if not all(COLOR.fullmatch(str(mode.get("background", ""))) for mode in (light, dark)):
        return None
    modes = {
        name: {token: value for token, value in mode.items() if isinstance(value, str) and COLOR.fullmatch(value)}
        for name, mode in (("light", light), ("dark", dark))
    }
    logo = object_data(data.get("logo"))
    image = ""
    if logo.get("light") and PurePosixPath(str(logo["light"])).suffix in {".png", ".webp", ".jpg"}:
        asset = (PurePosixPath(path).parent / relative(logo["light"])).as_posix()
        image = raw_url(source, asset)
    return {
        "name": text(data.get("name", "theme"), 80),
        "title": text(data.get("title", "Theme"), 100),
        "path": path,
        "palette": modes,
        "logo": image,
    }


def raw_url(source: Object, path: str) -> str:
    """Construct an immutable public asset URL from checked source identity."""
    return f"https://raw.githubusercontent.com/{source['repository']}/{source['revision']}/{relative(path)}"


def screenshot_data(source: Object, prefix: str, values: object) -> list[Object]:
    """Read labeled runtime screenshots from an owning project's catalog declaration.

    Raises:
        ValueError: If screenshots are malformed, oversized or lack a UI/CLI label.
    """
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > MAX_SCREENSHOTS:
        msg = "Screenshots must be a list of at most eight UI/CLI images"
        raise ValueError(msg)
    result = []
    for value in values:
        item = object_data(value)
        path = relative(item.get("path"))
        if item.get("surface") not in {"ui", "cli"} or PurePosixPath(path).suffix not in {".png", ".jpg", ".webp"}:
            msg = "Screenshots need a UI/CLI surface and a raster image"
            raise ValueError(msg)
        result.append(
            {
                "url": raw_url(source, prefix + path),
                "surface": item["surface"],
                "caption": text(item.get("caption", ""), 180),
            }
        )
    return result


def soundscape_data(path: str, data: Object) -> Object | None:
    """Read a YouTube soundscape without loading its plugin or contacting YouTube."""
    stream = object_data(data.get("stream"))
    video = stream.get("video", "")
    if data.get("kind") != "stream" or stream.get("provider") != "youtube":
        return None
    if not isinstance(video, str) or not VIDEO.fullmatch(video):
        return None
    return {
        "name": text(data.get("name", "soundscape"), 80),
        "title": text(data.get("title", "Soundscape"), 100),
        "video": video,
        "pairs_with": strings(data.get("pairs_with"), 24),
        "path": path,
    }


def project_record(source: Object, path: str, data: Object) -> Object | None:
    """Describe any declared plugin, including one the site has never seen before."""
    project = object_data(data.get("project"))
    points = object_data(object_data(project.get("entry-points")).get("heinzel.modules"))
    if not points:
        return None
    name = text(project.get("name"), 100).lower().replace("_", "-")
    if not NAME.fullmatch(name):
        return None
    owner = object_data(object_data(data.get("tool")).get("heinzel"))
    presentation, plugin = object_data(owner.get("catalog")), object_data(owner.get("plugin"))
    folder = PurePosixPath(path).parent.as_posix()
    prefix = "" if folder == "." else folder + "/"
    requirements = strings(project.get("dependencies"), 100)
    core = next((str(Requirement(r).specifier) for r in requirements if Requirement(r).name == "heinzel"), "undeclared")
    spec = f"git+https://github.com/{source['repository']}.git@{source['revision']}"
    if prefix:
        spec += f"#subdirectory={folder}"
    category = presentation.get("category", "connectors" if plugin.get("provides") else "utilities")
    if category not in CATEGORIES:
        category = "utilities"
    return {
        "name": name,
        "title": text(presentation.get("title", name.removeprefix("heinzel-").replace("-", " ").title()), 100),
        "id": name + "-" + hashlib.sha256(f"{source['repository']}:{folder}:{name}".encode()).hexdigest()[:10],
        "summary": text(project.get("description", "A Heinzelmännchen plugin.")),
        "category": category,
        "highlights": strings(presentation.get("highlights")),
        "version": text(project.get("version", "unversioned"), 60),
        "core": core,
        "declares_standard": "standard" in strings(plugin.get("profiles")),
        "included_version": "",
        "provides": strings(plugin.get("provides"), 24),
        "requires": strings(plugin.get("requires"), 24),
        "source": source,
        "directory": folder,
        "url": f"https://github.com/{source['repository']}/tree/{source['revision']}/{folder}",
        "install": f"hz system plugin add {shlex.quote(spec)}",
        "themes": [],
        "screenshots": screenshot_data(source, prefix, presentation.get("screenshots")),
        "soundscapes": [],
        "soundscape_paths": [prefix + relative(p) for p in strings(presentation.get("soundscapes"), 24)],
        "theme_paths": [prefix + relative(p) for p in strings(presentation.get("themes"))],
        "artwork": [
            {"url": raw_url(source, prefix + relative(p)), "alt": text(presentation.get("title", name), 100)}
            for p in strings(presentation.get("artwork"))
            if PurePosixPath(p).suffix in {".png", ".webp", ".jpg"}
        ],
    }


def project_themes(api: SourceBlobs, record: Object, data: Object, entries: dict[str, Object]) -> list[Object]:
    """Read only declared paths or inert theme data in explicitly packaged theme directories.

    Raises:
        ValueError: If a declared asset is missing or not a regular bounded file.
    """
    declared = record.pop("theme_paths")
    tool = object_data(data.get("tool"))
    targets = object_data(object_data(object_data(tool.get("hatch")).get("build")).get("targets"))
    packages = strings(object_data(targets.get("wheel")).get("packages"))
    prefix = "" if record["directory"] == "." else record["directory"] + "/"
    candidates = declared or [
        p
        for p in entries
        if any(p.startswith(prefix + relative(pkg) + "/themes/") for pkg in packages) and p.endswith(".toml")
    ]
    result = []
    for candidate in candidates[:24]:
        if candidate not in entries:
            msg = "A declared theme path does not exist in its public revision"
            raise ValueError(msg)
        theme = theme_data(
            record["source"],
            candidate,
            tomllib.loads(api.blob(record["source"]["repository"], entries[candidate]).decode()),
        )
        if theme:
            result.append(theme)
    for artwork in [*record["artwork"], *record["screenshots"]]:
        path = artwork["url"].split(f"/{record['source']['revision']}/", 1)[1]
        entry = entries.get(path, {})
        if entry.get("mode") not in {"100644", "100755"} or entry.get("size", MAX_ARTWORK + 1) > MAX_ARTWORK:
            msg = "Artwork must be a regular, bounded file in its owning public project"
            raise ValueError(msg)
    return result


def project_soundscapes(api: SourceBlobs, record: Object, data: Object, entries: dict[str, Object]) -> list[Object]:
    """Read declared soundscapes or inert TOML in explicitly packaged soundscape directories.

    Raises:
        ValueError: If a declared soundscape file is missing or unsafe.
    """
    declared = record.pop("soundscape_paths")
    targets = object_data(object_data(object_data(data.get("tool")).get("hatch")).get("build"))
    packages = strings(object_data(object_data(targets.get("targets")).get("wheel")).get("packages"))
    # Hatch's default package location is derived from the distribution name, not imported.
    packages = packages or ["src/" + record["name"].replace("-", "_")]
    prefix = "" if record["directory"] == "." else record["directory"] + "/"
    candidates = declared or [
        p
        for p in sorted(entries)
        if any(p.startswith(prefix + relative(pkg) + "/soundscapes/") for pkg in packages) and p.endswith(".toml")
    ]
    result = []
    for path in candidates[:24]:
        if path not in entries:
            msg = "A declared soundscape path does not exist in its public revision"
            raise ValueError(msg)
        value = soundscape_data(path, tomllib.loads(api.blob(record["source"]["repository"], entries[path]).decode()))
        if value:
            result.append(value)
    return result


def repository_records(api: GitHub, repo: str) -> tuple[Object, list[Object]]:
    """Discover public projects and their inert theme data at one revision.

    Raises:
        ValueError: If source identity or visibility is unsuitable for public publication.
    """
    if not REPOSITORY.fullmatch(repo):
        msg = "Catalog repositories must be GitHub owner/name pairs"
        raise ValueError(msg)
    metadata = api.get(f"repos/{repo}")
    if metadata.get("visibility") != "public" or metadata.get("private") is not False:
        msg = "A private repository cannot enter the public catalog"
        raise ValueError(msg)
    commit = api.get(f"repos/{repo}/commits/{quote(text(metadata['default_branch'], 200), safe='')}")
    sha = text(commit.get("sha"), 40)
    if not REVISION.fullmatch(sha):
        msg = "Catalog sources need an immutable commit"
        raise ValueError(msg)
    source = {"repository": repo, "revision": sha, "visibility": "public"}
    tree = api.get(f"repos/{repo}/git/trees/{sha}?recursive=1")
    if tree.get("truncated") or len(tree.get("tree", [])) > MAX_TREE:
        msg = "Catalog source tree is too large or truncated"
        raise ValueError(msg)
    entries = {text(item.get("path"), 240): item for item in tree.get("tree", [])}
    paths = [
        path
        for path in sorted(entries)
        if path == "pyproject.toml" or re.fullmatch(r"plugins/[A-Za-z0-9_.-]+/pyproject.toml", path)
    ]
    if len(paths) > MAX_SOURCE_PROJECTS:
        msg = "Catalog source exceeds its project budget"
        raise ValueError(msg)
    reader = SourceBlobs(api)
    records = []
    for path in paths:
        entry = entries[path]
        data = tomllib.loads(reader.blob(repo, entry).decode())
        record = project_record(source, path, data)
        if record is not None:
            record["themes"] = project_themes(reader, record, data, entries)
            record["soundscapes"] = project_soundscapes(reader, record, data, entries)
            if record["themes"]:
                record["category"] = "themes"
            records.append(record)
    return source, records


def public_json(url: str) -> Object:
    """Read a bounded public package/profile document, with no authentication header.

    Raises:
        ValueError: If the response exceeds the metadata limit.
    """
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - callers construct fixed official HTTPS URLs
        content = response.read(256 * 1024 + 1)
    if len(content) > 256 * 1024:
        msg = "Public release metadata is oversized"
        raise ValueError(msg)
    return object_data(json.loads(content))


def valid_artifact(artifact: Object, core_version: str) -> bool:
    """Mirror the installer's inert wheel identity contract without importing or installing core."""
    if set(artifact) != {"name", "version", "filename", "sha256", "url"}:
        return False
    name, version, filename = artifact["name"], artifact["version"], artifact["filename"]
    if (
        not isinstance(name, str)
        or not re.fullmatch(r"heinzel(?:-[a-z0-9]+(?:-[a-z0-9]+)*)?", name)
        or not isinstance(version, str)
        or len(version) > MAX_ARTIFACT_VERSION
        or not isinstance(filename, str)
        or len(filename) > MAX_ARTIFACT_FILENAME
        or not re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", filename)
        or not isinstance(artifact["sha256"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", artifact["sha256"])
    ):
        return False
    try:
        wheel_name, wheel_version, _, _ = parse_wheel_filename(filename)
    except InvalidWheelFilename:
        return False
    prefix = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/download/"
    return (
        wheel_name == canonicalize_name(name)
        and str(wheel_version) == version
        and artifact["url"] == f"{prefix}app-v{core_version}/{filename}"
    )


def complete_release(profile: Object, package: Object) -> bool:
    """Only recommend the profile when its core artifact matches the published package."""
    version = str(profile.get("core_version", ""))
    if (
        type(profile.get("format")) is not int
        or profile.get("format") != APP_PROFILE_FORMAT
        or profile.get("sdk") != 1
        or not VERSION.fullmatch(version)
        or set(profile) != {"format", "sdk", "core_version", "artifacts"}
    ):
        return False
    artifacts = profile.get("artifacts", [])
    if not isinstance(artifacts, list) or not MIN_PROFILE_ARTIFACTS <= len(artifacts) <= MAX_PROFILE_ARTIFACTS:
        return False
    seen = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not valid_artifact(artifact, version) or artifact["name"] in seen:
            return False
        seen.add(artifact["name"])
    core = [a for a in artifacts if a.get("name") == "heinzel" and a.get("version") == version]
    if len(core) != 1 or package.get("info", {}).get("version") != version:
        return False
    return any(
        a.get("filename") == core[0].get("filename")
        and not a.get("yanked", False)
        and a.get("digests", {}).get("sha256") == core[0].get("sha256")
        for a in package.get("urls", [])
    )


def recommended_release(api: GitHub, fallback: Object) -> Object:
    """Recommend only a complete profile whose core wheel matches its public PyPI package.

    Raises:
        ValueError: If no complete public installation can be verified.
    """
    repository = "repos/helmut-hoffer-von-ankershoffen/heinzel-plugins"
    refs = api.listing(f"{repository}/git/matching-refs/tags/app-v")
    versions = [str(r.get("ref", "")).removeprefix("refs/tags/app-v") for r in refs]
    if VERSION.fullmatch(str(fallback.get("version", ""))):
        versions.append(fallback["version"])
    candidates = sorted({v for v in versions if VERSION.fullmatch(v)}, key=Version, reverse=True)[:12]
    previous = str(fallback.get("version", ""))
    if VERSION.fullmatch(previous) and previous not in candidates:
        candidates.append(previous)
    for version in candidates:
        profile_url = f"https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/download/app-v{version}/standard.json"
        try:
            release = api.get(f"{repository}/releases/tags/app-v{version}")
            if release.get("draft") or release.get("prerelease") or release.get("tag_name") != f"app-v{version}":
                continue
            profile = public_json(profile_url)
            package = public_json(f"https://pypi.org/pypi/heinzel/{version}/json")
        except (OSError, ValueError):
            continue  # A release still rolling out cannot displace the last known complete recommendation.
        if not complete_release(profile, package):
            continue
        published = {a.get("name"): a.get("digest") for a in release.get("assets", [])}
        complete = all(
            published.get(a.get("filename")) == f"sha256:{a.get('sha256')}" for a in profile.get("artifacts", [])
        )
        if complete:
            return {
                "version": version,
                "install": f"uvx --from heinzel=={version} hz-install",
                "profile": profile_url,
                "artifacts": profile["artifacts"],
                "url": f"https://github.com/helmut-hoffer-von-ankershoffen/heinzel/releases/tag/v{version}",
            }
    msg = "No complete public installation is available"
    raise ValueError(msg)


def require_capacity(existing: list[Object], incoming: list[Object]) -> None:
    """Keep aggregation within the same bound the offline renderer accepts.

    Raises:
        ValueError: If the incoming source would crowd out a healthy catalog.
    """
    if len(existing) + len(incoming) > MAX_PROJECTS:
        msg = "Catalog aggregate exceeds its project budget"
        raise ValueError(msg)


def refresh(output: Path, *, api: GitHub | None = None) -> Object:
    """Refresh public metadata and preserve healthy entries when an optional source is invalid.

    Raises:
        ValueError: If required sources, discovery completeness or installation cannot be verified.
        OSError: If a required public source cannot be read.
        KeyError: If required source metadata is incomplete.
        TypeError: If a required source returned malformed metadata.
    """
    client = api or GitHub()
    configuration = tomllib.loads((ROOT / "docs" / "catalog-sources.toml").read_text())
    found = client.get("search/repositories?q=topic:heinzel-plugin+is:public+archived:false&per_page=50")
    if found.get("incomplete_results") or found.get("total_count", 0) > MAX_REPOSITORIES:
        msg = "Public topic discovery is incomplete; refusing a misleading refresh"
        raise ValueError(msg)
    repositories = set(configuration["repositories"])
    repositories.update(item["full_name"] for item in found.get("items", []))
    previous = json.loads(SNAPSHOT.read_text()) if SNAPSHOT.exists() else {}
    release = recommended_release(client, previous.get("release", {}))
    sources, projects, notices = [], [], []
    for repository in sorted(repositories, key=lambda repo: (repo not in configuration["repositories"], repo)):
        try:
            source, records = repository_records(client, repository)
            require_capacity(projects, records)
        except (ValueError, OSError, TypeError, KeyError):
            if repository in configuration["repositories"]:
                raise
            notices.append("An optional topic source could not be indexed and was omitted.")
            continue
        sources.append(source)
        projects.extend(records)
    if not projects:
        msg = "Refusing to publish an empty catalog"
        raise ValueError(msg)
    included = {a["name"]: a["version"] for a in release["artifacts"]}
    for project in projects:
        project["maintained"] = project["source"]["repository"] in configuration["repositories"]
        if project["source"]["repository"] == configuration["composition_repository"] and project["declares_standard"]:
            project["included_version"] = included.get(project["name"], "")
    result = {
        "format": 1,
        "refreshed_at": datetime.now(UTC).isoformat(),
        "sources": sources,
        "notices": notices,
        "release": release,
        "plugins": sorted(projects, key=lambda p: p["name"]),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    # Replace only after the complete refresh succeeds; never leave a partial JSON artifact.
    with tempfile.NamedTemporaryFile(mode="w", dir=output.parent, delete=False, encoding="utf-8") as temporary:
        temporary.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        staged = Path(temporary.name)
    try:
        staged.replace(output)
    finally:
        staged.unlink(missing_ok=True)
    return result


def main() -> None:
    """Refresh an explicit public snapshot; ordinary site builds require no network."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=SNAPSHOT)
    args = parser.parse_args()
    snapshot = refresh(args.output)
    print(f"Public catalog: {len(snapshot['plugins'])} plugins from {len(snapshot['sources'])} sources")


if __name__ == "__main__":
    main()
