"""Installed, versioned view bundles: data declarations and bounded local asset reads (ADR 0094)."""

import hashlib
import os
import re
import stat
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import TypeAdapter

from heinzel.kernel._contributions import Effect
from heinzel.kernel._lets import NAME_PATTERN, Let

VIEW_ABI = 1
MAX_VIEW_FILE = 4 * 1024 * 1024
MAX_VIEW_FILES = 64
MAX_VIEW_TOTAL = 12 * 1024 * 1024
MAX_ORDER = 9999
MAX_REFRESH = 86400
MAX_TEXT = 280
MAX_ACTIONS = 8
ViewSlot = Literal["page", "panel", "sidebar", "settings", "background"]
_ASSET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,240}$")


def _parts(name: str) -> tuple[str, ...]:
    parts = PurePosixPath(name).parts
    if not _ASSET.fullmatch(name) or not parts or any(p in {".", ".."} or p.startswith(".") for p in parts):
        msg = "A view asset needs a relative, nonhidden file path"
        raise ValueError(msg)
    return parts


def _read(root: Path, name: str) -> bytes:
    parts = _parts(name)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            inner = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = inner
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                msg = "A view asset must be a regular file"
                raise ValueError(msg)
            content = handle.read(MAX_VIEW_FILE + 1)
        if len(content) > MAX_VIEW_FILE:
            msg = "A view asset exceeds four MiB"
            raise ValueError(msg)
        return content
    finally:
        os.close(directory)


@dataclass(frozen=True, kw_only=True, slots=True)
class ViewLet(Let):
    """A module-owned factory bundle; the head supplies the shared SDK, never another React runtime."""

    directory: Path
    source: str
    title: str
    fallback: str
    entry: str = "index.js"
    abi: int = VIEW_ABI
    slot: ViewSlot = "panel"
    order: int = 500
    refresh: int = 60
    section: str | None = None
    route: str | None = None
    actions: dict[str, Effect] = field(default_factory=dict[str, Effect])
    diagnostic: str | None = field(init=False, default=None)
    fingerprint: str = field(init=False)
    files: tuple[tuple[str, str], ...] = field(init=False, repr=False)

    def _validate_metadata(self) -> None:
        """Validate metadata and fingerprint exactly the files the head may serve.

        Raises:
            ValueError: If metadata, paths, sizes or the entry module are invalid.
        """
        if not NAME_PATTERN.fullmatch(self.name) or not NAME_PATTERN.fullmatch(self.source):
            msg = "View and source names must be module operation names"
            raise ValueError(msg)
        self._validate_numbers()
        for text in (self.title, self.fallback):
            if not text.strip() or len(text) > MAX_TEXT or any(not c.isprintable() for c in text):
                msg = "View titles and fallbacks need bounded, printable text"
                raise ValueError(msg)
        if self.route is not None and (not re.fullmatch(r"/[a-z0-9][a-z0-9/-]*", self.route) or "//" in self.route):
            msg = "A view route must be a fixed local page path"
            raise ValueError(msg)
        if self.slot == "page" and self.route is None:
            msg = "A page view must declare its route"
            raise ValueError(msg)
        self._validate_actions()
        if not self.entry.endswith(".js"):
            msg = "A view entry must be a JavaScript module"
            raise ValueError(msg)
        _parts(self.entry)

    def _validate_numbers(self) -> None:
        if any(type(value) is not int for value in (self.abi, self.order, self.refresh)):
            msg = "View ABI, order and refresh must be strict integers"
            raise ValueError(msg)
        if self.slot not in {"page", "panel", "sidebar", "settings", "background"} or self.abi < 1:
            msg = "Unsupported view slot or SDK ABI"
            raise ValueError(msg)
        if not 0 <= self.order <= MAX_ORDER or not 1 <= self.refresh <= MAX_REFRESH:
            msg = "View order or refresh is out of bounds"
            raise ValueError(msg)

    def _validate_actions(self) -> None:
        if len(self.actions) > MAX_ACTIONS or any(not NAME_PATTERN.fullmatch(name) for name in self.actions):
            msg = "A view has at most eight named actions"
            raise ValueError(msg)
        for action in self.actions.values():
            TypeAdapter(Effect).validate_python(action)

    def __post_init__(self) -> None:
        """Validate the declaration without making a broken optional view hide Python operations."""
        self._validate_metadata()
        if not self.directory.exists():
            # Editable Python installs remain useful before the independent frontend build has run.
            object.__setattr__(self, "files", ())
            object.__setattr__(self, "fingerprint", "")
            return
        try:
            files, fingerprint = _bundle(self.directory, self.entry)
        except (OSError, ValueError):
            files, fingerprint = (), ""
            object.__setattr__(self, "diagnostic", "Build this plugin's view bundle before opening its view")
        object.__setattr__(self, "files", files)
        object.__setattr__(self, "fingerprint", fingerprint)

    def prepared(self) -> "ViewLet":
        """Recheck an editable declaration after its independent frontend build."""
        return self if self.files else replace(self)


def _bundle(directory: Path, entry: str) -> tuple[tuple[tuple[str, str], ...], str]:
    files: list[tuple[str, str]] = []
    total = 0
    digest = hashlib.sha256()
    try:
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                msg = "View bundles cannot contain symbolic links"
                raise ValueError(msg)
            if path.is_dir():
                continue
            name = path.relative_to(directory).as_posix()
            content = _read(directory, name)
            total += len(content)
            if total > MAX_VIEW_TOTAL or len(files) >= MAX_VIEW_FILES:
                msg = "View bundle exceeds its file or total size limit"
                raise ValueError(msg)
            files.append((name, hashlib.sha256(content).hexdigest()))
            digest.update(name.encode() + b"\0" + content)
        if entry not in dict(files):
            msg = "View bundle entry is missing; build the plugin's web assets first"
            raise ValueError(msg)
    except OSError as exc:
        msg = "View bundle cannot be read safely"
        raise ValueError(msg) from exc
    return tuple(files), digest.hexdigest()


def read_view_asset(view: ViewLet, name: str) -> bytes:
    """Read only declared, unchanged regular files, checking every directory relative to an open descriptor.

    Raises:
        ValueError: If a path, file or digest no longer matches its declaration.
        OSError: If a directory or file was replaced or cannot be read.
    """
    expected = dict(view.files).get(name)
    if expected is None:
        msg = "No declared view asset"
        raise ValueError(msg)
    content = _read(view.directory, name)
    if hashlib.sha256(content).hexdigest() != expected:
        msg = "View asset changed after discovery; rebuild the plugin and restart"
        raise ValueError(msg)
    return content
