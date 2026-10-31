"""Cached update eligibility and installed frontend identity; installation still uses the existing system operation."""

import hashlib
import json
import re
import stat
from datetime import UTC, datetime
from html.parser import HTMLParser
from importlib import metadata
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

from heinzel import __version__
from heinzel.kernel import Installation, Kernel, ReleaseCache

MAX_FILES = 128
MAX_ASSET_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_HTML_BYTES = 1024 * 1024
MAX_INSTALLER_BYTES = 16
_ASSET = re.compile(r"/assets/[A-Za-z0-9_.-]+\Z")


class Frontend(BaseModel):
    """The startup build's entrypoints and bytes, independent of any browser cache."""

    version: str = Field(description="The code version of the server that loaded this frontend build.")
    identity: str = Field(description="SHA256 of the canonical entrypoint and asset-hash descriptor.")
    entrypoints: list[str] = Field(
        description="Same-origin script and stylesheet paths declared by the installed HTML."
    )
    files: dict[str, str] = Field(description="Installed asset paths and their SHA256 hashes; no API or user files.")


class UpdateStatus(BaseModel):
    """Cached release availability and whether the existing installation supports an explicit web update."""

    current: str = Field(description="The version this server actually loaded.")
    latest: str | None = Field(description="Latest cached release, absent when checks are disabled or never succeeded.")
    installed_at: datetime | None = Field(
        description="When uv installed the current core, from its installation metadata; absent when unknown."
    )
    available: bool = Field(description="Whether the cached release is newer than this server.")
    supported: bool = Field(description="Whether a persistent uv tool, source policy and frontend can be updated here.")
    reason: str | None = Field(description="Why a manual update is required; contains no private installation source.")


class _Entrypoints(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.paths: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        # Inline scripts (for example, the connection splash bootstrap) are not
        # installed asset entrypoints. Ignore them; only external JS and CSS
        # files can establish the frontend identity used by safe updates.
        if tag == "script":
            src = values.get("src")
            if src:
                self.paths.append(src)
        elif tag == "link" and values.get("rel") == "stylesheet":
            href = values.get("href")
            if href:
                self.paths.append(href)


def _asset_hashes(assets: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    total = 0
    for asset in assets.iterdir():
        name = f"/assets/{asset.name}"
        if not _ASSET.fullmatch(name) or asset.is_symlink() or not asset.is_file():
            msg = "Unsafe installed frontend asset"
            raise ValueError(msg)
        size = asset.stat().st_size
        total += size
        if len(files) >= MAX_FILES or size > MAX_ASSET_BYTES or total > MAX_TOTAL_BYTES:
            msg = "Installed frontend exceeds descriptor budget"
            raise ValueError(msg)
        files[name] = hashlib.sha256(asset.read_bytes()).hexdigest()
    return files


def _frontend(static: Path | None) -> Frontend | None:
    if static is None:
        return None
    index, assets = static / "index.html", static / "assets"
    try:
        if index.is_symlink() or assets.is_symlink() or index.stat().st_size > MAX_HTML_BYTES:
            return None
        parser = _Entrypoints()
        parser.feed(index.read_text(encoding="utf-8"))
        entrypoints = sorted(set(parser.paths))
        if not entrypoints or not all(_ASSET.fullmatch(path) for path in entrypoints):
            return None
        files = _asset_hashes(assets)
        if not set(entrypoints) <= files.keys():
            return None
    except (OSError, UnicodeError, ValueError):
        return None  # An incomplete installed build must not enable automatic reload or imply verification.
    descriptor = json.dumps({"entrypoints": entrypoints, "files": files}, sort_keys=True, separators=(",", ":"))
    return Frontend(
        version=__version__,
        identity=hashlib.sha256(descriptor.encode()).hexdigest(),
        entrypoints=entrypoints,
        files=files,
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _installed_at(installation: Installation) -> datetime | None:
    """Use uv's installed core metadata, not a release date or a plugin's more recent receipt change.

    This is a best-effort timestamp: migrated or copied environments may preserve an older file time. Other
    installers, editable sources, missing metadata and metadata outside the running tool return no date.
    """
    if not installation.is_tool:
        return None
    try:
        dist = metadata.distribution("heinzel")
        marker = next((file for file in dist.files or () if str(file).endswith(".dist-info/INSTALLER")), None)
        if dist.version != __version__ or marker is None:
            return None
        path = Path(marker.locate()).resolve(strict=True)
        if not path.is_relative_to(installation.prefix.resolve()):
            return None
        info = path.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size > MAX_INSTALLER_BYTES
            or path.read_text(encoding="utf-8").strip() != "uv"
        ):
            return None
        stamp = datetime.fromtimestamp(info.st_mtime, UTC)
        return stamp if 0 < stamp.timestamp() <= _now().timestamp() else None
    except (OSError, UnicodeError, OverflowError, ValueError, metadata.PackageNotFoundError):
        return None


def add_updates(app: FastAPI, kernel: Kernel, static: Path | None, *, restartable: bool) -> None:
    """Expose only authenticated transport facts; never refresh releases or run the updater during a read."""
    frontend = _frontend(static)
    installation = kernel.resolve(Installation)
    installed_at = _installed_at(installation)

    @app.get("/update-status", tags=["web"])
    def status(response: Response) -> UpdateStatus:
        response.headers["Cache-Control"] = "no-store"
        cache = ReleaseCache(kernel.settings.cache_dir)
        enabled = kernel.settings.update_check
        pin = installation.core_pin()
        source_allowed = pin is not None and (
            pin.kind in {"release", "default", "index"} or pin.is_edge or (pin.kind == "ref" and pin.ref == "main")
        )
        reason = (
            "Restart this UI with hz ui before updating."
            if not restartable
            else "Frontend assets are incomplete, so hz cannot safely update this install. For uv, run `uv tool "
            "upgrade heinzel`; otherwise use your installer. Then restart `hz ui`."
            if frontend is None
            else "This installation is deliberately pinned or is not a persistent uv tool; update it where installed."
            if not source_allowed
            else None
        )
        return UpdateStatus(
            current=__version__,
            latest=cache.latest if enabled else None,
            installed_at=installed_at,
            available=enabled and cache.newer_than(__version__) is not None,
            supported=reason is None,
            reason=reason,
        )

    @app.get("/frontend", tags=["web"])
    def build(response: Response) -> Frontend:
        if frontend is None:
            raise HTTPException(status_code=409, detail="This server has no verifiable installed frontend build.")
        response.headers["Cache-Control"] = "no-store"
        return frontend
