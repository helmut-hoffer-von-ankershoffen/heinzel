"""Service of the bookmark module: one ordered tree per owner in the data directory, favicons in the cache.

The tree is owner data (`<data_dir>/bookmark/bookmarks.json`), never in the desktop browser's partition, so clearing
browsing data keeps it. A removed entry stays restorable until newer removals push it out.
"""

import base64
import binascii
import contextlib
import fcntl
import hashlib
import json
import os
import secrets
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from heinzel import HeinzelError, Kernel
from heinzel.kernel import clean_text
from heinzel.modules.system.bookmark._models import Bookmark, BookmarkList

MAX_ENTRIES = 2000
MAX_DEPTH = 8
MAX_TITLE = 200
MAX_URL = 2048
MAX_REMOVED = 20
MAX_FAVICON_BYTES = 6000
"""A favicon arrives in a request line, and a 32-pixel image fits well below this."""
MAX_FAVICONS = 500
_IMAGES = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/x-icon": (b"\x00\x00\x01\x00",),
}
_SUFFIX = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/x-icon": "ico"}
PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600
type Node = dict[str, Any]


def _private_dir(path: Path) -> None:
    """Create `path` for the owner alone; one that exists is narrowed to the owner too."""
    path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR)
    path.chmod(PRIVATE_DIR)


def _write_private(path: Path, data: bytes) -> None:
    """Replace `path` with `data` through a sibling file only the owner can read."""
    temporary = path.with_name(f".{path.name}-{secrets.token_hex(4)}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_FILE)
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _well_formed(node: object) -> bool:
    """Whether a stored entry has the shape every operation relies on, all the way down."""
    if not isinstance(node, dict):
        return False
    entry = cast("dict[str, Any]", node)
    if not isinstance(entry.get("id"), str) or not isinstance(entry.get("title"), str):
        return False
    if entry.get("kind") == "bookmark":
        return isinstance(entry.get("url"), str)
    children = entry.get("children")
    return (
        entry.get("kind") == "folder"
        and isinstance(children, list)
        and all(map(_well_formed, cast("list[object]", children)))
    )


def _well_formed_removal(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    entry = cast("dict[str, Any]", item)
    return (
        _well_formed(entry.get("node"))
        and isinstance(entry.get("parent"), str | None)
        and isinstance(entry.get("index"), int)
    )


def bookmark_url(value: str) -> str:
    """The address as stored: HTTPS, with a host, no credentials, bounded.

    Raises:
        HeinzelError: If it is anything else.
    """
    text = value.strip()
    try:
        parts = urlsplit(text)
        host = parts.hostname
    except ValueError:
        host, parts = None, None
    if parts is None or parts.scheme != "https" or not host or parts.username or parts.password:
        msg = "A bookmark needs an https:// address with a host and no sign-in details in it."
        raise HeinzelError(msg)
    if len(text) > MAX_URL or any(ord(char) < 0x21 or ord(char) == 0x7F for char in text):  # noqa: PLR2004
        msg = f"A bookmark's address has at most {MAX_URL} characters and no spaces or control characters."
        raise HeinzelError(msg)
    return text


def origin_of(url: str) -> str:
    """`https://host[:port]` of an address already checked by `bookmark_url`."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    return f"https://{host}" + (f":{parts.port}" if parts.port not in (None, 443) else "")


def _title(value: str, fallback: str) -> str:
    text = " ".join(clean_text(value).split())[:MAX_TITLE]
    return text or fallback


def favicon_image(value: str) -> tuple[str, bytes]:
    """The media type and bytes of a favicon given as a base64 `data:` URL.

    Raises:
        HeinzelError: If it isn't a small PNG, JPEG, GIF or ICO image.
    """
    head, _, payload = value.partition(",")
    media = head.removeprefix("data:").removesuffix(";base64").lower()
    if media == "image/vnd.microsoft.icon":
        media = "image/x-icon"
    problem = "A favicon is a PNG, JPEG, GIF or ICO image as a base64 data: URL of at most "
    problem += f"{MAX_FAVICON_BYTES} bytes."
    if not head.startswith("data:") or not head.endswith(";base64") or media not in _IMAGES:
        raise HeinzelError(problem)
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise HeinzelError(problem) from None
    if not data or len(data) > MAX_FAVICON_BYTES or not data.startswith(_IMAGES[media]):
        raise HeinzelError(problem)
    return media, data


class BookmarkService:
    """The owner's bookmark tree and the favicon cache."""

    def __init__(self, kernel: Kernel) -> None:
        """Create the service; the kernel is injected for the owner's directories."""
        self._file = kernel.settings.data_dir / "bookmark" / "bookmarks.json"
        self._icons = kernel.settings.cache_dir / "bookmark" / "favicons"

    # --- storage ---

    @contextlib.contextmanager
    def _locked(self) -> Generator[None]:
        _private_dir(self._file.parent)
        descriptor = os.open(self._file.parent / "bookmarks.lock", os.O_WRONLY | os.O_CREAT | os.O_APPEND, PRIVATE_FILE)
        with os.fdopen(descriptor, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _read(self) -> Node:
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "children": [], "removed": []}
        except (OSError, ValueError) as exc:
            msg = f"The bookmarks file {self._file} can't be read ({exc}); move it away to start again."
            raise HeinzelError(msg) from exc
        if not isinstance(data, dict):
            msg = f"The bookmarks file {self._file} has an unknown format; move it away to start again."
            raise HeinzelError(msg)
        document = cast("Node", data)
        children: object = document.get("children")
        removed: object = document.get("removed", [])
        if (
            document.get("version") != 1
            or not isinstance(children, list)
            or not isinstance(removed, list)
            or not all(map(_well_formed, cast("list[object]", children)))
            or not all(map(_well_formed_removal, cast("list[object]", removed)))
        ):
            msg = f"The bookmarks file {self._file} has an unknown format; move it away to start again."
            raise HeinzelError(msg)
        document["removed"] = removed
        return document

    def _write(self, data: Node) -> None:
        _write_private(self._file, json.dumps(data, ensure_ascii=False, indent=1).encode())

    # --- tree helpers ---

    @staticmethod
    def _walk(
        children: list[Node], parent: str | None = None, depth: int = 0
    ) -> Iterator[tuple[Node, str | None, int]]:
        for node in children:
            yield node, parent, depth
            if node["kind"] == "folder":
                yield from BookmarkService._walk(node["children"], node["id"], depth + 1)

    def _find(self, data: Node, entry: str) -> tuple[Node, list[Node], int]:
        """The node, the list holding it and its depth.

        Raises:
            HeinzelError: If no entry has that id.
        """

        def search(children: list[Node], depth: int) -> tuple[Node, list[Node], int] | None:
            for node in children:
                if node["id"] == entry:
                    return node, children, depth
                if node["kind"] == "folder" and (found := search(node["children"], depth + 1)):
                    return found
            return None

        if found := search(data["children"], 0):
            return found
        msg = f"No bookmark or folder has the id {entry!r}; `hz system bookmark list` shows them."
        raise HeinzelError(msg)

    def _folder(self, data: Node, parent: str | None) -> tuple[list[Node], int]:
        """The children of the folder `parent` (the top level for none) and the depth entries get there.

        Raises:
            HeinzelError: If `parent` is unknown or a bookmark.
        """
        if parent is None:
            return data["children"], 0
        node, _, depth = self._find(data, parent)
        if node["kind"] != "folder":
            msg = f"{parent!r} is a bookmark, not a folder."
            raise HeinzelError(msg)
        return node["children"], depth + 1

    @staticmethod
    def _height(node: Node) -> int:
        """Levels the entry takes: 1 for a bookmark; a folder counts the level below it too, even while empty."""
        if node["kind"] != "folder":
            return 1
        return 1 + max((BookmarkService._height(child) for child in node["children"]), default=1)

    @staticmethod
    def _require_room(depth: int, height: int) -> None:
        """The one depth rule of add, folder, move and restore.

        Raises:
            HeinzelError: If an entry of `height` levels at `depth` would nest deeper than `MAX_DEPTH`.
        """
        if depth + height > MAX_DEPTH:
            msg = f"Folders nest at most {MAX_DEPTH - 1} deep."
            raise HeinzelError(msg)

    @staticmethod
    def _insert(children: list[Node], node: Node, before: str | None) -> None:
        if before is None:
            children.append(node)
            return
        for index, sibling in enumerate(children):
            if sibling["id"] == before:
                children.insert(index, node)
                return
        msg = f"{before!r} isn't in that folder, so nothing can go before it."
        raise HeinzelError(msg)

    def _new(self, data: Node, kind: str, title: str, url: str | None) -> Node:
        if sum(1 for _ in self._walk(data["children"])) >= MAX_ENTRIES:
            msg = f"There are {MAX_ENTRIES} bookmarks and folders already; remove some first."
            raise HeinzelError(msg)
        taken = {node["id"] for node, _, _ in self._walk(data["children"])}
        taken |= {node["id"] for item in data["removed"] for node, _, _ in self._walk([item["node"]])}
        while (entry := f"b-{secrets.token_hex(4)}") in taken:
            pass
        node: Node = {"id": entry, "kind": kind, "title": title}
        if kind == "folder":
            node["children"] = []
        else:
            node["url"] = url
        return node

    def _view(self, data: Node, changed: str | None = None) -> BookmarkList:
        bookmarks = [
            Bookmark(
                id=node["id"],
                kind=node["kind"],
                title=node["title"],
                url=node.get("url"),
                origin=origin_of(node["url"]) if node.get("url") else None,
                parent=parent,
                depth=depth,
            )
            for node, parent, depth in self._walk(data["children"])
        ]
        origins = sorted({bookmark.origin for bookmark in bookmarks if bookmark.origin})
        favicons = {origin: icon for origin in origins if (icon := self._favicon(origin))}
        restorable = [item["node"]["id"] for item in reversed(data["removed"])]
        return BookmarkList(bookmarks=bookmarks, favicons=favicons, restorable=restorable, changed=changed)

    # --- operations ---

    def listing(self) -> BookmarkList:
        """The whole tree."""
        return self._view(self._read())

    def add(self, url: str, title: str | None, parent: str | None, before: str | None) -> BookmarkList:
        """Add a bookmark; an address already in that folder is kept once and returned.

        Raises:
            HeinzelError: If the address, the folder or the place is refused.
        """
        address = bookmark_url(url)
        with self._locked():
            data = self._read()
            children, depth = self._folder(data, parent)
            self._require_room(depth, 1)
            if same := next((node for node in children if node.get("url") == address), None):
                return self._view(data, same["id"])
            node = self._new(data, "bookmark", _title(title or "", urlsplit(address).hostname or address), address)
            self._insert(children, node, before)
            self._write(data)
            return self._view(data, node["id"])

    def folder(self, title: str, parent: str | None, before: str | None) -> BookmarkList:
        """Add an empty folder.

        Raises:
            HeinzelError: If the folder or the place is refused.
        """
        with self._locked():
            data = self._read()
            children, depth = self._folder(data, parent)
            self._require_room(depth, 2)
            node = self._new(data, "folder", _title(title, "Folder"), None)
            self._insert(children, node, before)
            self._write(data)
            return self._view(data, node["id"])

    def edit(self, entry: str, title: str | None, url: str | None) -> BookmarkList:
        """Rename an entry or change a bookmark's address.

        Raises:
            HeinzelError: If the entry is unknown or the address refused.
        """
        with self._locked():
            data = self._read()
            node, _, _ = self._find(data, entry)
            if url is not None:
                if node["kind"] == "folder":
                    msg = "A folder has no address."
                    raise HeinzelError(msg)
                node["url"] = bookmark_url(url)
            if title is not None:
                node["title"] = _title(title, node["title"])
            self._write(data)
            return self._view(data, entry)

    def move(self, entry: str, parent: str | None, before: str | None) -> BookmarkList:
        """Move an entry into `parent` (the top level for none), before `before` or to the end.

        Raises:
            HeinzelError: If the move is refused; nothing changes then.
        """
        with self._locked():
            data = self._read()
            node, holder, _ = self._find(data, entry)
            if before == entry:
                return self._view(data, entry)
            if parent is not None and any(inner["id"] == parent for inner, _, _ in self._walk([node])):
                msg = "A folder can't move into itself or a folder inside it."
                raise HeinzelError(msg)
            children, depth = self._folder(data, parent)
            self._require_room(depth, self._height(node))
            index = holder.index(node)
            holder.remove(node)
            try:
                self._insert(children, node, before)
            except HeinzelError:
                holder.insert(index, node)
                raise
            self._write(data)
            return self._view(data, entry)

    def remove(self, entry: str) -> BookmarkList:
        """Remove an entry, a folder with everything in it; `restore` brings it back."""
        with self._locked():
            data = self._read()
            node, holder, _ = self._find(data, entry)
            parent = next((p for found, p, _ in self._walk(data["children"]) if found is node), None)
            index = holder.index(node)
            holder.remove(node)
            data["removed"] = [*data["removed"], {"node": node, "parent": parent, "index": index}][-MAX_REMOVED:]
            self._write(data)
            return self._view(data)

    def restore(self, entry: str | None) -> BookmarkList:
        """Bring back a removed entry, the latest one for none, to where it was if that folder still exists.

        Raises:
            HeinzelError: If nothing is kept under that id, or the tree is full.
        """
        with self._locked():
            data = self._read()
            removed: list[Node] = data["removed"]
            item = next((i for i in reversed(removed) if entry in (None, i["node"]["id"])), None)
            if item is None:
                msg = "Nothing to restore: no removed bookmark or folder is kept under that id."
                raise HeinzelError(msg)
            try:
                children, depth = self._folder(data, item["parent"])
            except HeinzelError:
                children, depth = data["children"], 0
            if depth + self._height(item["node"]) > MAX_DEPTH:
                children = data["children"]
            if sum(1 for _ in self._walk(data["children"])) + sum(1 for _ in self._walk([item["node"]])) > MAX_ENTRIES:
                msg = f"Restoring it would pass {MAX_ENTRIES} bookmarks and folders; remove some first."
                raise HeinzelError(msg)
            removed.remove(item)
            children.insert(min(max(int(item["index"]), 0), len(children)), item["node"])
            self._write(data)
            return self._view(data, item["node"]["id"])

    # --- favicons ---

    def _icon_stem(self, origin: str) -> str:
        return hashlib.sha256(origin.encode()).hexdigest()[:32]

    def _favicon(self, origin: str) -> str | None:
        stem = self._icon_stem(origin)
        for media, suffix in _SUFFIX.items():
            try:
                data = (self._icons / f"{stem}.{suffix}").read_bytes()
            except OSError:
                continue
            if 0 < len(data) <= MAX_FAVICON_BYTES and data.startswith(_IMAGES[media]):
                return f"data:{media};base64,{base64.b64encode(data).decode()}"
        return None

    def set_favicon(self, origin: str, image: str) -> BookmarkList:
        """Cache the favicon a page of `origin` showed; only for origins that have a bookmark.

        Raises:
            HeinzelError: If the image is refused or no bookmark has that origin.
        """
        media, data = favicon_image(image)
        with self._locked():
            tree = self._read()
            known = {origin_of(node["url"]) for node, _, _ in self._walk(tree["children"]) if node.get("url")}
            if origin not in known:
                msg = "A favicon is kept only for the origin of a bookmark, e.g. https://example.com."
                raise HeinzelError(msg)
            _private_dir(self._icons.parent)
            _private_dir(self._icons)
            stem = self._icon_stem(origin)
            # Icons of origins without a bookmark go, so the cache follows the tree and stays bounded.
            keep = {self._icon_stem(item) for item in known}
            for path in self._icons.iterdir():
                if path.stem not in keep or path.stem == stem:
                    path.unlink(missing_ok=True)
            if len(keep) <= MAX_FAVICONS:
                _write_private(self._icons / f"{stem}.{_SUFFIX[media]}", data)
            return self._view(tree)

    @property
    def file(self) -> Path:
        """Where the tree is kept."""
        return self._file
