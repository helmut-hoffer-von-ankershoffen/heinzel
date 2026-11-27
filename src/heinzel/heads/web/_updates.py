"""Cached update eligibility and installed frontend identity; installation still uses the existing system operation."""

import hashlib
import json
import logging
import platform
import re
import stat
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from html.parser import HTMLParser
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Request, Response
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, Field

from heinzel import __version__
from heinzel._adapter_system import SYSTEM_CLOCK, SYSTEM_RUNNER
from heinzel.kernel import HeinzelError, Installation, Kernel, ReleaseCache, subprocess_environment
from heinzel.kernel._desktop_app import DesktopVariant, desktop_app_here
from heinzel.kernel._logging import log_event

if TYPE_CHECKING:
    from heinzel.ports import RunningProcess

_log = logging.getLogger(__name__)
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


class DesktopAppStatus(BaseModel):
    """The desktop app on this Mac and whether it is older than this server."""

    version: str | None = Field(description="The app's version; none if its bundle doesn't say.")
    variant: DesktopVariant | None = Field(description="native or electron; none for another bundle.")
    behind: bool = Field(
        description="Whether the app is older than this server, so `Update desktop app` installs this release."
    )


class DesktopUpdateStarted(BaseModel):
    """The desktop app's update was started in its own process; the app quits, is replaced and opens again."""

    started: bool = Field(description="Always true: `hz app install --restart` runs on its own now.")
    command: str = Field(description="The command that runs, the same a person could type.")


class UpdateStatus(BaseModel):
    """Cached release availability and whether the existing installation supports an explicit web update."""

    current: str = Field(description="The version this server actually loaded.")
    latest: str | None = Field(description="Latest cached release, absent when checks are disabled or never succeeded.")
    installed_at: datetime | None = Field(
        description="When uv installed the current core, from its installation metadata; absent when unknown."
    )
    available: bool = Field(description="Whether the cached release is newer than this server.")
    supported: bool = Field(description="Whether a persistent uv tool, source policy and frontend can be updated here.")
    checked_at: datetime | None = Field(
        default=None, description="When the release source was last asked, whether or not it answered."
    )
    last_ok_at: datetime | None = Field(
        default=None, description="When the release source last answered; None if it never did on this machine."
    )
    check_failed: bool = Field(
        default=False, description="Whether the last time the release source was asked it did not answer."
    )
    reason: str | None = Field(description="Why a manual update is required; contains no private installation source.")
    installed: str | None = Field(
        default=None,
        description="The core version the installation holds when it differs from the one this server loaded, so a "
        "verified restart of this server loads it; None when they match or the installation can't be verified.",
    )
    idle: bool = Field(
        default=True,
        description="Whether no chat answer, install or daemon job runs now, the same check automatic updates wait "
        "for; the app restarts on its own only while idle.",
    )
    desktop: DesktopAppStatus | None = Field(
        default=None, description="The desktop app on this Mac and whether it is out of date; none without one."
    )


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
    return SYSTEM_CLOCK.now()


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


def _installed_core(installation: Installation) -> str | None:
    """The installed core version when it is verified and differs from the loaded one; None otherwise.

    Reading the metadata starts no code. Any doubt (not a uv tool, unreadable or changing metadata) answers None, so
    the app never restarts on a guess.
    """
    if not installation.is_tool:
        return None
    try:
        installed = installation.installed_core_version()
        differs = Version(installed) != Version(__version__)
    except (HeinzelError, InvalidVersion, OSError) as exc:
        log_event(_log, "web.update.installed_core", outcome="unverified", error=type(exc).__name__)
        return None
    return installed if differs else None


def _desktop_status() -> DesktopAppStatus | None:
    """The desktop app here and whether it is older than this server; a version that can't be read is not behind."""
    found = desktop_app_here()
    if found is None or found.variant is None:
        return None
    try:
        behind = found.version is not None and Version(found.version) < Version(__version__)
    except InvalidVersion:
        behind = False
    return DesktopAppStatus(version=found.version, variant=found.variant, behind=behind)


DESKTOP_COMMAND = "hz app install --restart"
DESKTOP_UPDATE_PATH = "/update-status/desktop"


def _start_desktop_update() -> "RunningProcess":
    """Run this hz's `hz app install --restart` in its own session, so it outlives the app it quits and reopens.

    The installer asks the app to quit, waits, never forces it, replaces it and opens it again; it logs every step.
    """
    flags = ["-I", "-B"] if sys.dont_write_bytecode else ["-I"]
    return SYSTEM_RUNNER.spawn(
        [sys.executable, *flags, "-m", "heinzel", "app", "install", "--restart"],
        env=subprocess_environment() | {"HZ_UPDATE_CHECK": "false"},
        detached=True,
    )


def add_updates(
    app: FastAPI,
    kernel: Kernel,
    static: Path | None,
    *,
    restartable: bool,
    busy: Callable[[], bool] = lambda: False,
) -> None:
    """Expose only authenticated transport facts; never refresh releases or run the updater during a read."""
    frontend = _frontend(static)
    installation = kernel.resolve(Installation)
    installed_at = _installed_at(installation)
    _add_desktop_update(app, kernel, restartable=restartable, busy=busy)

    @app.get("/update-status", tags=["web"])
    def status(response: Response) -> UpdateStatus:
        response.headers["Cache-Control"] = "no-store"
        cache = ReleaseCache(kernel.settings.cache_dir)
        # Read now, not as booted: Settings saves these two without a restart.
        current = kernel.fresh_settings()
        enabled = current.update_check
        pin = installation.core_pin()
        source_allowed = pin is not None and (
            pin.kind in {"release", "default", "index"} or pin.is_edge or (pin.kind == "ref" and pin.ref == "main")
        )
        reason = (
            "Restart this UI with hz ui before updating."
            if not restartable
            else "Frontend assets are incomplete, so hz cannot safely update this install. For uv, run `uv tool "
            "upgrade heinzel`; otherwise use your installer. Then restart `hz ui serve`."
            if frontend is None
            else "This installation is deliberately pinned or is not a persistent uv tool; update it where installed."
            if not source_allowed
            else None
        )
        return UpdateStatus(
            current=__version__,
            latest=cache.latest if enabled else None,
            installed_at=installed_at,
            available=enabled and cache.newer_than(__version__, current.update_channel) is not None,
            supported=reason is None,
            checked_at=cache.checked_at if enabled else None,
            last_ok_at=cache.ok_at if enabled else None,
            check_failed=enabled and cache.error is not None,
            reason=reason,
            installed=_installed_core(installation) if restartable else None,
            idle=not busy(),
            desktop=_desktop_status(),
        )

    @app.get("/frontend", tags=["web"])
    def build(response: Response) -> Frontend:
        if frontend is None:
            raise HTTPException(status_code=409, detail="This server has no verifiable installed frontend build.")
        response.headers["Cache-Control"] = "no-store"
        return frontend


def _add_desktop_update(app: FastAPI, kernel: Kernel, *, restartable: bool, busy: Callable[[], bool]) -> None:
    """Register the detached desktop installer and coalesce concurrent requests."""
    desktop_update_lock = threading.Lock()
    desktop_update_process: RunningProcess | None = None

    @app.post(DESKTOP_UPDATE_PATH, tags=["web"], status_code=202)
    def update_desktop(request: Request, automatic: bool = False) -> DesktopUpdateStarted:
        """Update the desktop app to this server's release: quit it, replace it and open it again.

        \f

        Raises:
            HTTPException: 409 without an out-of-date desktop app here, 500 if the update can't be started.
        """  # noqa: D301 - FastAPI ends the description at a real form feed
        nonlocal desktop_update_process
        with desktop_update_lock:
            desktop = _desktop_status()
            if platform.system() != "Darwin" or desktop is None:
                raise HTTPException(status_code=409, detail="There is no desktop app here to update.")
            if not desktop.behind:
                raise HTTPException(status_code=409, detail=f"The desktop app is already at {desktop.version}.")
            if automatic:
                settings = kernel.fresh_settings()
                if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
                    raise HTTPException(status_code=409, detail="Only the local workspace can restart the Mac app.")
                if not (
                    restartable
                    and settings.auto_update
                    and settings.update_check
                    and settings.update_channel == "stable"
                ):
                    raise HTTPException(status_code=409, detail="Automatic desktop updates are off.")
                if busy():
                    raise HTTPException(status_code=409, detail="Heinzel is busy. Restart the app when ready.")
            if desktop_update_process is None or desktop_update_process.poll() is not None:
                try:
                    desktop_update_process = _start_desktop_update()
                except OSError as exc:
                    log_event(_log, "web.update.desktop", outcome="error", error=type(exc).__name__)
                    raise HTTPException(status_code=500, detail="The desktop app update couldn't be started.") from exc
        log_event(_log, "web.update.desktop", outcome="started", before=desktop.version, target=__version__)
        return DesktopUpdateStarted(started=True, command=DESKTOP_COMMAND)
