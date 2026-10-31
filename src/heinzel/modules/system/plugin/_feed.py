"""The published static plugin catalog: first-party discovery and previews without a GitHub API request.

The catalog is a JSON document the maintainers publish next to the documentation. Reading it costs no GitHub rate
limit, so Discover and screenshot previews of official plugins keep working for everyone behind a shared address. It
is cached on disk and revalidated with a conditional request; if it cannot be read, the caller falls back to GitHub.
Nothing from it is executed: only bounded text, public revisions and fixed-host image links are used.
"""

import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any, cast, override

from packaging.utils import canonicalize_name

from heinzel import HeinzelError, __version__
from heinzel.modules.system.plugin._models import PluginScreenshot, PluginShowcase, PluginSoundscape
from heinzel.modules.system.plugin._sources import PluginSource

MAX_FEED = 4 * 1024 * 1024
FRESH_SECONDS = 600
MAX_ENTRIES = 500
MAX_IMAGES = 8
MAX_SOUNDS = 24
TIMEOUT = 10
RAW = "https://raw.githubusercontent.com"
REVISION = re.compile(r"[a-f0-9]{40}")
VIDEO = re.compile(r"[A-Za-z0-9_-]{11}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
RELATIVE = re.compile(r"[A-Za-z0-9_./-]{1,240}")


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    @override
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _fetch(url: str, etag: str | None) -> tuple[int, str | None, bytes]:
    """GET the catalog (never following redirects), conditionally; returns the status, ETag and body.

    Raises:
        HTTPError: If the server answers with an error.
        OSError: If it cannot be read.
        ValueError: If the answer is too large.
    """
    if not url.startswith("https://"):
        msg = "The published catalog must be an https URL"
        raise ValueError(msg)
    headers = {"Accept": "application/json", "User-Agent": f"heinzel/{__version__}"}
    if etag:
        headers["If-None-Match"] = etag
    opener = urllib.request.build_opener(_NoRedirects)
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=TIMEOUT) as response:  # noqa: S310 - https
            body: bytes = response.read(MAX_FEED + 1)
            status = int(response.status)
            new = response.headers.get("ETag")
    except urllib.error.HTTPError as exc:
        if exc.code == 304:  # noqa: PLR2004 - not modified
            exc.close()
            return 304, etag, b""
        raise
    if len(body) > MAX_FEED:
        msg = "The published catalog is too large"
        raise ValueError(msg)
    return status, new, body


def load_feed(url: str, cache_dir: Path, *, now: float | None = None) -> list[dict[str, Any]]:
    """The catalog's plugin entries: from the disk cache while fresh, else revalidated, else the stale copy.

    Raises:
        OSError: If it is unreachable and nothing is cached.
        ValueError: If the document is malformed.
    """
    now = time.time() if now is None else now
    path = cache_dir / "plugin-catalog" / "feed.json"
    cached: dict[str, Any] = {}
    try:
        if path.stat().st_size <= MAX_FEED * 2:
            value = json.loads(path.read_text())
            cached = cast("dict[str, Any]", value) if isinstance(value, dict) else {}
    except (OSError, ValueError):
        cached = {}
    if cached.get("url") != url or not isinstance(cached.get("document"), dict):
        cached = {}
    if cached and 0 <= now - float(cached.get("fetched", 0)) < FRESH_SECONDS:
        return _plugins(cached["document"])
    try:
        status, etag, body = _fetch(url, cached.get("etag") if cached else None)
    except (OSError, ValueError):
        if cached:
            return _plugins(cached["document"])  # stale beats nothing: it names public revisions
        raise
    document: object = cached["document"] if status == 304 and cached else json.loads(body)  # noqa: PLR2004
    plugins = _plugins(document)
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"url": url, "etag": etag, "fetched": now, "document": document}))
        temporary.replace(path)
    except OSError:
        pass  # the cache is an optimisation
    return plugins


def _plugins(document: object) -> list[dict[str, Any]]:
    if not isinstance(document, dict):
        msg = "The published catalog is not an object"
        raise ValueError(msg)  # noqa: TRY004 - decoded data, not a type error
    fields = cast("dict[str, Any]", document)
    values = fields.get("plugins")
    if fields.get("format") != 1 or not isinstance(values, list) or len(cast("list[object]", values)) > MAX_ENTRIES:
        msg = "The published catalog has an unknown format"
        raise ValueError(msg)
    return [cast("dict[str, Any]", value) for value in cast("list[object]", values) if isinstance(value, dict)]


def _table(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _text(value: object, limit: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or any(unicodedata.category(char) in {"Cc", "Cf"} for char in value)
    ):
        message = "Published preview captions must be bounded plain text"
        raise HeinzelError(message)
    return value


def _directory(value: object) -> str:
    return "" if value in {None, "", "."} else str(value).strip("/")


def _identity(entry: dict[str, Any]) -> tuple[str, str, str, str] | None:
    """(repository, directory, revision, distribution) of a public entry; None for anything unsafe."""
    source = _table(entry.get("source"))
    repository, revision, name = source.get("repository"), source.get("revision"), entry.get("name")
    if (
        source.get("visibility") != "public"
        or not isinstance(repository, str)
        or not REPOSITORY.fullmatch(repository)
        or not isinstance(revision, str)
        or not REVISION.fullmatch(revision)
        or not isinstance(name, str)
    ):
        return None
    return repository, _directory(entry.get("directory")), revision, name


def entry_for(source: PluginSource, plugins: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The public catalog entry owning exactly this repository, directory and distribution; else None."""
    if source.github is None:
        return None
    wanted = "/".join(source.github).casefold()
    directory = _directory(source.subdirectory)
    for entry in plugins:
        identity = _identity(entry)
        if (
            identity
            and identity[0].casefold() == wanted
            and identity[1] == directory
            and (not source.name or canonicalize_name(identity[3]) == source.name)
        ):
            return entry
    return None


def feed_showcase(source: PluginSource, plugins: list[dict[str, Any]]) -> PluginShowcase | None:
    """The preview a published entry declares, when it describes the revision this source names; else None.

    A source pinned to another revision (an installed plugin older or newer than the catalog) is never answered from
    the catalog: its own revision is read instead, so installed-source precedence holds.

    Raises:
        HeinzelError: If the entry's declared media are unsafe.
    """
    entry = entry_for(source, plugins)
    identity = _identity(entry) if entry else None
    if entry is None or identity is None:
        return None
    repository, directory, revision, _ = identity
    if source.ref is not None and source.ref != revision:
        return None
    base = f"{RAW}/{repository}/{revision}/" + (directory + "/" if directory else "")
    images = entry.get("screenshots", [])
    sounds = entry.get("soundscapes", [])
    if not isinstance(images, list) or not isinstance(sounds, list):
        return None
    if len(cast("list[object]", images)) > MAX_IMAGES or len(cast("list[object]", sounds)) > MAX_SOUNDS:
        message = "A published preview is larger than allowed"
        raise HeinzelError(message)
    screenshots: list[PluginScreenshot] = []
    for value in cast("list[object]", images):
        item = _table(value)
        url = item.get("url")
        relative = url[len(base) :] if isinstance(url, str) and url.startswith(base) else ""
        if (
            not RELATIVE.fullmatch(relative)
            or any(part in {".", "..", ""} for part in relative.split("/"))
            or PurePosixPath(relative).suffix not in {".png", ".jpg", ".webp"}
            or item.get("surface") not in {"ui", "cli"}
        ):
            message = "A published preview image does not belong to its plugin revision"
            raise HeinzelError(message)
        screenshots.append(
            PluginScreenshot(url=cast("str", url), surface=item["surface"], caption=_text(item.get("caption"), 180))
        )
    soundscapes = [
        PluginSoundscape(title=_text(item.get("title"), 100), url=f"https://www.youtube.com/watch?v={item['video']}")
        for item in (_table(value) for value in cast("list[object]", sounds))
        if isinstance(item.get("video"), str) and VIDEO.fullmatch(item["video"])
    ]
    return PluginShowcase(
        title=_text(entry.get("title") or identity[3], 100),
        revision=revision,
        screenshots=screenshots,
        soundscapes=soundscapes,
        notice="Screenshots and soundscapes are declared by the owning public plugin.",
    )


def _raw_request(url: str, method: str, limit: int) -> tuple[bytes, Any]:
    """One anonymous request to raw.githubusercontent.com; no token ever, no redirect.

    Raises:
        LookupError: If it is not there (or the repository is not public: raw serves public repositories only).
        HTTPError: If the server answers with another error.
        OSError: If it cannot be read.
        ValueError: If the URL is not on raw.githubusercontent.com or the answer is too large.
    """
    if not url.startswith(f"{RAW}/"):
        msg = f"not a raw.githubusercontent.com URL: {url}"
        raise ValueError(msg)
    request = urllib.request.Request(url, method=method, headers={"User-Agent": f"heinzel/{__version__}"})  # noqa: S310
    try:
        with urllib.request.build_opener(_NoRedirects).open(request, timeout=TIMEOUT) as response:
            body: bytes = response.read(limit + 1) if method == "GET" else b""
            headers = response.headers
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        if code == 404:  # noqa: PLR2004 - not found
            raise LookupError(url) from None
        raise
    if len(body) > limit:
        msg = "The file is larger than its limit"
        raise ValueError(msg)
    return body, headers


def raw_text(url: str, limit: int) -> str:
    """A small public text file at an immutable revision."""
    return _raw_request(url, "GET", limit)[0].decode()


def raw_image_size(url: str) -> tuple[int, str]:
    """(bytes, content type) of a public file, without downloading it."""
    _, headers = _raw_request(url, "HEAD", 0)
    length = headers.get("Content-Length", "")
    return (int(length) if length.isdigit() else -1), str(headers.get("Content-Type", "")).split(";")[0].strip()
