"""What `hz system about` and the About dialog show: versions, install source, daemon, plugins and their pins.

One report for every head: the CLI prints its `diagnostics` lines, the web app's About dialog shows its fields and
copies the same lines plus what only the page knows (its own web build and the desktop app it runs in).
"""

import datetime as dt
import hashlib
import json
import logging
import platform
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from packaging.utils import canonicalize_name
from pydantic import BaseModel, Field

import heinzel
from heinzel import Kernel, __version__
from heinzel.kernel import CorePin, DaemonAttestation, HeinzelError, Installation
from heinzel.kernel._logging import log_event
from heinzel.modules.system._service import _installed_on, _source_of

if TYPE_CHECKING:
    from rich.console import RenderableType

logger = logging.getLogger(__name__)

WEB_INDEX = Path(heinzel.__file__).parent / "heads" / "web" / "static" / "index.html"
"""The web app this installation serves (`make web-build` or the wheel puts it there); read, never imported."""
DESKTOP_RECEIPT = Path(".config/heinzel/desktop-installation.json")
"""Under the home folder: the managed Mac app's receipt, naming the release it installed."""
MAX_INDEX_BYTES = 1024 * 1024
MAX_RECEIPT_BYTES = 64 * 1024
BUILD_LENGTH = 12
SHORT_COMMIT = 12


class AboutDaemon(BaseModel):
    """The background daemon, as `hz system daemon status` sees it."""

    state: Literal["running", "paused", "stopped", "not installed", "unknown"] = Field(
        description="`running` (loaded and ticking), `paused`, `stopped` (installed but not loaded), `not installed`, "
        "or `unknown` when the service manager could not be asked."
    )
    version: str | None = Field(description="The hz version its last tick ran, else the one it was installed with.")
    outdated: bool = Field(
        default=False, description="Whether it runs another hz than this one and is waiting for its restart."
    )


class AboutPlugin(BaseModel):
    """An installed plugin, where it came from and whether an update may move it."""

    name: str = Field(description="Distribution name.")
    version: str = Field(description="Installed version.")
    source: Literal["PyPI", "git", "path"] = Field(description="Installed from an index (PyPI), git or a path.")
    pinned: str | None = Field(
        description="What its install spec pins it to (a tag, a commit, a file or an exact version); none if updates "
        "move it."
    )
    installed: dt.date | None = Field(description="The day this version was installed here; none if unknown.")


class AboutReport(BaseModel):
    """What `hz system about` prints and the About dialog shows: versions, install source, daemon and plugins."""

    version: str = Field(description="The Heinzelmännchen core version.")
    installed: dt.date | None = Field(description="The day it was installed here; none if unknown.")
    installation: Literal["uv tool", "environment"] = Field(
        description="Whether hz runs from a uv tool environment or from another Python environment."
    )
    source: Literal["PyPI", "git", "path"] = Field(description="Where the core was installed from.")
    channel: str = Field(description="The update channel it follows (setting `update_channel`).")
    web_build: str | None = Field(
        description="Short id of the web app build this installation serves; none if it has no built web app."
    )
    desktop: str | None = Field(description="Release of the desktop app hz installed on this Mac; none otherwise.")
    daemon: AboutDaemon = Field(description="The background daemon.")
    plugins: list[AboutPlugin] = Field(description="Every installed plugin distribution, built-ins excluded.")
    python_version: str = Field(description="Version of the Python running hz.")
    platform: str = Field(description="Operating system and architecture.")
    diagnostics: list[str] = Field(description="The same facts as plain lines, for pasting into a bug report.")


class _Entrypoints(HTMLParser):
    """The hashed `/assets/` scripts and stylesheets an index page loads; inline scripts are no part of a build."""

    def __init__(self) -> None:
        super().__init__()
        self.paths: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        path = values.get("src") if tag == "script" else values.get("href") if tag == "link" else None
        if path and path.startswith("/assets/") and (tag == "script" or values.get("rel") == "stylesheet"):
            self.paths.append(path)


def web_build_of(html: str) -> str | None:
    """A web build's short id: SHA-256 over its sorted entrypoints, one per line; None for a page with none.

    The web app computes the same id from the page it runs (`web/src/updates/webBuild.ts`), so a page can tell that
    the server now serves another build.
    """
    parser = _Entrypoints()
    parser.feed(html)
    paths = sorted(set(parser.paths))
    return hashlib.sha256("\n".join(paths).encode()).hexdigest()[:BUILD_LENGTH] if paths else None


def served_web_build(index: Path = WEB_INDEX) -> str | None:
    """The build of the web app this installation serves; None without one (a checkout that never built it)."""
    try:
        if index.is_symlink() or index.stat().st_size > MAX_INDEX_BYTES:
            return None
        return web_build_of(index.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return None


def installed_desktop(home: Path | None = None) -> str | None:
    """The release of the desktop app hz installed on this Mac, from its receipt; None elsewhere or without one."""
    if platform.system() != "Darwin":
        return None
    try:
        path = (home or Path.home()) / DESKTOP_RECEIPT
        if path.stat().st_size > MAX_RECEIPT_BYTES:
            return None
        release = json.loads(path.read_text(encoding="utf-8")).get("release")
    except (OSError, RuntimeError, ValueError, AttributeError):
        return None
    return release if isinstance(release, str) and release else None


def pinned_label(pin: CorePin) -> str | None:
    """What a plugin's install spec pins it to, in a few words; None when an update may move it.

    Mirrors what `hz system update` keeps (a tag, a commit, a file or an exact version); a branch or the index's newest
    release follows along. A local path is named as such, never spelled out: diagnostics get pasted.
    """
    ref = pin.ref or ""
    match pin.kind:
        case "release":
            return ref
        case "commit":
            return f"commit {ref[:SHORT_COMMIT]}"
        case "local":
            return "local folder"
        case "url":
            return "file or URL"
        case "index" if ref.startswith("=="):
            return ref
        case _:
            return None


def _daemon(kernel: Kernel) -> AboutDaemon:
    """The daemon's state as `hz system daemon status` sees it; `unknown` when it can't be asked."""
    if not kernel.provides(DaemonAttestation):
        return AboutDaemon(state="unknown", version=None)
    try:
        status = kernel.resolve(DaemonAttestation).status()
    # Isolation boundary: the About report never fails because the service manager could not be asked.
    except (HeinzelError, OSError, ValueError) as exc:
        log_event(logger, "system.about.daemon", outcome="unknown", error_type=type(exc).__name__)
        return AboutDaemon(state="unknown", version=None)
    # `loaded` and `paused` are no part of the attestation contract; the daemon module's status carries them.
    loaded, paused = getattr(status, "loaded", True), getattr(status, "paused", False)
    state: Literal["running", "paused", "stopped", "not installed"]
    if not status.installed:
        state = "not installed"
    elif not loaded:
        state = "stopped"
    else:
        state = "paused" if paused else "running"
    return AboutDaemon(
        state=state,
        version=status.version or status.installed_version,
        outdated=status.installed and status.version_mismatch,
    )


def _plugins(kernel: Kernel, installation: Installation) -> list[AboutPlugin]:
    """Every installed plugin distribution with its install source and pin, by name."""
    try:
        pins = {canonicalize_name(pin.name.split("[", 1)[0]): pin for pin in installation.plugin_pins()}
    except (HeinzelError, OSError, ValueError, KeyError, TypeError):  # an unreadable receipt pins nothing it can show
        pins = {}
    found: dict[str, AboutPlugin] = {}
    for record in kernel.modules:
        origin = record.origin
        if origin.builtin or origin.distribution in found:
            continue
        pin = pins.get(canonicalize_name(origin.distribution))
        found[origin.distribution] = AboutPlugin(
            name=origin.distribution,
            version=origin.version,
            source=_source_of(origin.distribution),
            pinned=pinned_label(pin) if pin else None,
            installed=_installed_on(origin.distribution),
        )
    return sorted(found.values(), key=lambda plugin: plugin.name)


def diagnostics(report: AboutReport) -> list[str]:
    """The report as plain lines, the same the CLI prints and the About dialog copies."""
    lines = [
        f"hz {report.version}" + (f" (installed {report.installed.isoformat()})" if report.installed else ""),
        f"install {report.installation}, from {report.source}, channel {report.channel}",
        f"web build {report.web_build or 'none'}",
        f"desktop app {report.desktop or 'not installed'}",
        "daemon "
        + report.daemon.state
        + (f" hz {report.daemon.version}" if report.daemon.version else "")
        + (" (needs a restart)" if report.daemon.outdated else ""),
        f"python {report.python_version}",
        f"platform {report.platform}",
    ]
    lines += [
        f"plugin {plugin.name} {plugin.version} from {plugin.source}"
        + (f", pinned to {plugin.pinned}" if plugin.pinned else ", follows updates")
        for plugin in report.plugins
    ]
    return lines


def render_about(report: AboutReport) -> "RenderableType":
    """The report's plain lines, the same the About dialog copies; terminal controls in a name are shown escaped."""
    from rich.text import Text  # noqa: PLC0415 - CLI presentation is loaded only when rendered

    return Text("\n".join(report.diagnostics).translate(_CONTROLS))


_CONTROLS = {code: f"\\x{code:02x}" for code in [*range(0x20), 0x7F] if code != ord("\n")}
"""Every C0 control and DEL but the line break, as its escape: a plugin's name or version can't steer the terminal."""


def about_report(kernel: Kernel, installation: Installation, home: Path | None = None) -> AboutReport:
    """Collect the report; nothing in it fails the whole, each part says `unknown` or none instead."""
    report = AboutReport(
        version=__version__,
        installed=_installed_on("heinzel"),
        installation="uv tool" if installation.is_tool else "environment",
        source=_source_of("heinzel"),
        channel=kernel.settings.update_channel,
        web_build=served_web_build(),
        desktop=installed_desktop(home),
        daemon=_daemon(kernel),
        plugins=_plugins(kernel, installation),
        python_version=platform.python_version(),
        platform=platform.platform(),
        diagnostics=[],
    )
    report.diagnostics = diagnostics(report)
    log_event(logger, "system.about.read", outcome="ok", plugins=len(report.plugins), daemon=report.daemon.state)
    return report


__all__ = [
    "AboutDaemon",
    "AboutPlugin",
    "AboutReport",
    "about_report",
    "diagnostics",
    "installed_desktop",
    "pinned_label",
    "render_about",
    "served_web_build",
    "web_build_of",
]
