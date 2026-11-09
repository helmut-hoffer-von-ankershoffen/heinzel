"""Read inert public showcase metadata at immutable GitHub revisions without running plugin code."""

import base64
import re
import tomllib
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, cast
from urllib.parse import quote

from packaging.utils import canonicalize_name

from heinzel import HeinzelError
from heinzel.modules.system.plugin._models import PluginScreenshot, PluginShowcase, PluginSoundscape
from heinzel.modules.system.plugin._sources import JsonGetter, PluginSource

MAX_METADATA = 96 * 1024
MAX_ASSET = 2 * 1024 * 1024
MAX_TREE = 12000
MAX_IMAGES = 8
MAX_SOUNDS = 24
REVISION = re.compile(r"[a-f0-9]{40}")
VIDEO = re.compile(r"[A-Za-z0-9_-]{11}")


def _table(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _items(value: object, limit: int, message: str) -> list[object]:
    if not isinstance(value, list):
        raise HeinzelError(message)
    result = cast("list[object]", value)
    if len(result) > limit:
        raise HeinzelError(message)
    return result


def _relative(value: object) -> str:
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9_./-]{1,240}", value)
        or PurePosixPath(value).is_absolute()
        or any(part in {".", "..", ""} for part in value.split("/"))
    ):
        message = "Preview asset paths must remain inside their owning project"
        raise HeinzelError(message)
    return value


def _text(value: object, limit: int) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or any(unicodedata.category(char) in {"Cc", "Cf"} for char in value)
    ):
        message = "Preview captions must be bounded plain text"
        raise HeinzelError(message)
    return value


@dataclass(frozen=True, slots=True)
class _PublicSource:
    base: str
    owner: str
    repository: str
    revision: str
    prefix: str
    entries: dict[str, dict[str, Any]]
    get: JsonGetter

    def blob(self, path: str) -> dict[str, Any]:
        entry = self.entries.get(path, {})
        sha = entry.get("sha", "")
        if (
            entry.get("mode") not in {"100644", "100755"}
            or entry.get("type") != "blob"
            or not isinstance(sha, str)
            or not REVISION.fullmatch(sha)
            or type(entry.get("size")) is not int
            or not 0 <= entry["size"] <= MAX_METADATA
        ):
            message = "Showcase metadata must be a bounded regular file in its public revision"
            raise HeinzelError(message)
        data = _table(self.get(f"{self.base}/git/blobs/{sha}"))
        encoded = data.get("content", "")
        if not isinstance(encoded, str) or len(encoded) > MAX_METADATA * 2:
            message = "Showcase metadata exceeds its size limit"
            raise HeinzelError(message)
        content = base64.b64decode("".join(encoded.split()), validate=True)
        if len(content) > MAX_METADATA:
            message = "Showcase metadata exceeds its size limit"
            raise HeinzelError(message)
        return tomllib.loads(content.decode())

    def path(self, value: object) -> str:
        return self.prefix + _relative(value)


def public_showcase(source: PluginSource, get: JsonGetter) -> PluginShowcase:
    """Project one source's checked UI, CLI and soundscape declarations into a safe preview.

    Raises:
        HeinzelError: If public source identities or declared media are unsafe.
        OSError: If the public source cannot be read.
        ValueError: If source metadata cannot be decoded.
    """
    if source.github is None:
        return PluginShowcase(title=source.name or "Plugin", notice="Open the owning repository to see its previews.")
    owner, repository = source.github
    base = f"repos/{quote(owner)}/{quote(repository)}"
    metadata = _table(get(base))
    if metadata.get("private") is not False or metadata.get("visibility") != "public":
        return PluginShowcase(
            title=source.name or "Plugin", notice="Private-source images stay in their owning repository."
        )
    ref = source.ref or str(metadata.get("default_branch", "HEAD"))
    revision = _table(get(f"{base}/commits/{quote(ref, safe='')}")).get("sha", "")
    if not isinstance(revision, str) or not REVISION.fullmatch(revision):
        message = "Screenshot previews require an immutable public source revision"
        raise HeinzelError(message)
    tree = _table(get(f"{base}/git/trees/{revision}?recursive=1"))
    message = "This source is too large to preview safely; open its repository instead"
    values = _items(tree.get("tree", []), MAX_TREE, message)
    if tree.get("truncated"):
        message = "This source is too large to preview safely; open its repository instead"
        raise HeinzelError(message)
    entries = {str(item.get("path")): item for value in values if (item := _table(value))}
    prefix = _relative(source.subdirectory) + "/" if source.subdirectory else ""
    public = _PublicSource(base, owner, repository, revision, prefix, entries, get)
    data = public.blob(public.path("pyproject.toml"))
    project = _table(data.get("project"))
    name = project.get("name")
    if not isinstance(name, str) or (source.name and canonicalize_name(name) != source.name):
        message = "The preview source does not match the selected plugin distribution"
        raise HeinzelError(message)
    catalog = _table(_table(_table(data.get("tool")).get("heinzel")).get("catalog"))
    title = _text(catalog.get("title", name or "Plugin"), 100)
    return PluginShowcase(
        title=title,
        revision=revision,
        screenshots=_screenshots(catalog, public),
        soundscapes=_soundscapes(catalog, data, public, name),
        notice="Screenshots and soundscapes are declared by the owning public plugin.",
    )


def _screenshots(catalog: dict[str, Any], source: _PublicSource) -> list[PluginScreenshot]:
    declarations = _items(
        catalog.get("screenshots", []), MAX_IMAGES, "A plugin preview may declare at most eight screenshots"
    )
    images: list[PluginScreenshot] = []
    for value in declarations:
        item = _table(value)
        path = source.path(item.get("path"))
        entry = source.entries.get(path, {})
        if (
            PurePosixPath(path).suffix not in {".png", ".jpg", ".webp"}
            or item.get("surface") not in {"ui", "cli"}
            or entry.get("type") != "blob"
            or entry.get("mode") not in {"100644", "100755"}
            or type(entry.get("size")) is not int
            or not 0 <= entry["size"] <= MAX_ASSET
        ):
            message = "Preview screenshots must be bounded regular raster images with UI or CLI labels"
            raise HeinzelError(message)
        images.append(
            PluginScreenshot(
                url=f"https://raw.githubusercontent.com/{source.owner}/{source.repository}/{source.revision}/{path}",
                surface=item["surface"],
                caption=_text(item.get("caption", "Screenshot"), 180),
            )
        )
    return images


def _soundscapes(
    catalog: dict[str, Any], data: dict[str, Any], source: _PublicSource, name: str
) -> list[PluginSoundscape]:
    declared = _items(catalog.get("soundscapes", []), MAX_SOUNDS, "Soundscape previews must be bounded")
    paths = [source.path(value) for value in declared]
    if not paths:
        targets = _table(_table(_table(_table(data.get("tool")).get("hatch")).get("build")).get("targets"))
        packages = _items(
            _table(targets.get("wheel")).get("packages", ["src/" + name.replace("-", "_")]),
            MAX_SOUNDS,
            "Soundscape package declarations must be bounded",
        )
        if packages:
            paths = [
                path
                for path in sorted(source.entries)
                if path.endswith(".toml")
                and any(path.startswith(source.path(package) + "/soundscapes/") for package in packages)
            ]
    sounds: list[PluginSoundscape] = []
    for path in paths[:MAX_SOUNDS]:
        sound = source.blob(path)
        stream = _table(sound.get("stream"))
        video = stream.get("video", "")
        if (
            sound.get("kind") == "stream"
            and stream.get("provider") == "youtube"
            and isinstance(video, str)
            and VIDEO.fullmatch(video)
        ):
            sounds.append(
                PluginSoundscape(
                    title=_text(sound.get("title", "Soundscape"), 100), url=f"https://www.youtube.com/watch?v={video}"
                )
            )
    return sounds


RawText = Callable[[str, int], str]
"""Reads a small public text file by URL (raises LookupError when it is not there)."""
RawSize = Callable[[str], tuple[int, str]]
"""The byte size and content type of a public file by URL, without downloading it."""


def raw_showcase(
    source: PluginSource, text: RawText, size: RawSize, hints: Sequence[str] = ()
) -> tuple[PluginShowcase, bool] | None:
    """Preview a public source pinned to an immutable revision from raw.githubusercontent.com, with no API request.

    Anonymous raw access only serves public repositories, so a file that is there proves the source is public.
    Returns the preview and whether soundscapes the project does not declare still need its file tree (the API) to be
    discovered; None when the source is not a pinned GitHub revision. `hints` are project-relative soundscape paths
    the published catalog knows at another revision: each is read at this revision, so a hint can only ever reveal
    what this revision really contains (a soundscape added since is not listed, and the notice says so).

    Raises:
        HeinzelError: If public source identities or declared media are unsafe.
        OSError: If the public source cannot be read.
        ValueError: If source metadata cannot be decoded.
    """
    if source.github is None or not source.ref or not REVISION.fullmatch(source.ref):
        return None
    owner, repository = source.github
    prefix = _relative(source.subdirectory) + "/" if source.subdirectory else ""
    base = f"https://raw.githubusercontent.com/{quote(owner)}/{quote(repository)}/{source.ref}/{prefix}"
    try:
        data = tomllib.loads(text(base + "pyproject.toml", MAX_METADATA))
    except LookupError:
        return (
            PluginShowcase(
                title=source.name or "Plugin",
                notice="No public previews: this revision is private or not readable. Open its owning repository.",
            ),
            False,
        )
    project = _table(data.get("project"))
    name = project.get("name")
    if not isinstance(name, str) or (source.name and canonicalize_name(name) != source.name):
        message = "The preview source does not match the selected plugin distribution"
        raise HeinzelError(message)
    catalog = _table(_table(_table(data.get("tool")).get("heinzel")).get("catalog"))
    images = _raw_images(base, catalog, size)
    declared = _items(catalog.get("soundscapes", []), MAX_SOUNDS, "Soundscape previews must be bounded")
    sounds = _raw_sounds(base, [_relative(path) for path in declared], text)
    targets = _table(_table(_table(data.get("tool")).get("hatch")).get("build")).get("targets")
    discover = not declared and bool(_table(_table(targets).get("wheel")).get("packages", ["src"]))
    notice = "Screenshots and soundscapes are declared by the owning public plugin."
    if discover and hints:
        sounds = _raw_sounds(base, [_relative(path) for path in hints[:MAX_SOUNDS]], text, skip_missing=True)
        discover = False
        notice += " Soundscapes are those the published catalog lists; a newer one may be missing."
    title = _text(catalog.get("title", name), 100)
    return (
        PluginShowcase(
            title=title,
            revision=source.ref,
            screenshots=images,
            soundscapes=sounds,
            notice=notice,
        ),
        discover,
    )


def _sound(sound: dict[str, Any]) -> PluginSoundscape | None:
    stream = _table(sound.get("stream"))
    video = stream.get("video", "")
    if (
        sound.get("kind") == "stream"
        and stream.get("provider") == "youtube"
        and isinstance(video, str)
        and VIDEO.fullmatch(video)
    ):
        return PluginSoundscape(
            title=_text(sound.get("title", "Soundscape"), 100), url=f"https://www.youtube.com/watch?v={video}"
        )
    return None


def _raw_images(base: str, catalog: dict[str, Any], size: RawSize) -> list[PluginScreenshot]:
    declarations = _items(
        catalog.get("screenshots", []), MAX_IMAGES, "A plugin preview may declare at most eight screenshots"
    )
    images: list[PluginScreenshot] = []
    for value in declarations:
        item = _table(value)
        relative = _relative(item.get("path"))
        url = base + relative
        try:
            length, kind = size(url)
        except LookupError:
            length, kind = -1, ""
        if (
            PurePosixPath(relative).suffix not in {".png", ".jpg", ".webp"}
            or item.get("surface") not in {"ui", "cli"}
            or not 0 <= length <= MAX_ASSET
            or not kind.startswith("image/")
        ):
            message = "Preview screenshots must be bounded regular raster images with UI or CLI labels"
            raise HeinzelError(message)
        images.append(
            PluginScreenshot(url=url, surface=item["surface"], caption=_text(item.get("caption", "Screenshot"), 180))
        )
    return images


def _raw_sounds(base: str, paths: list[str], text: RawText, *, skip_missing: bool = False) -> list[PluginSoundscape]:
    sounds: list[PluginSoundscape] = []
    for path in paths:
        try:
            link = _sound(tomllib.loads(text(base + path, MAX_METADATA)))
        except LookupError:
            if skip_missing:
                continue
            raise
        if link:
            sounds.append(link)
    return sounds
