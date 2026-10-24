# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Build Heinzel Notifier, hz's macOS notifier app, into the package: `make notifier` (ADR 0043).

Compiles src/heinzel/modules/system/notify/macos/main.swift for arm64 and x86_64 (macOS 13+), joins them with lipo,
draws the icon from heinzel.svg with icon.swift and iconutil, writes Info.plist with the sources' digest as
CFBundleVersion and signs the bundle ad hoc (`codesign --sign -`). The result, HeinzelNotifier.app beside the
sources, is not in git; the wheel ships it. The app runs on macOS 13 or later; building it needs macOS 14 or later
(AppKit reads SVG from 14 on) with the Xcode Command Line Tools (`xcode-select --install`); elsewhere it says so and
exits 1.

`icons` (`make icons`) makes everything else that shows Heinzelmännchen from that one heinzel.svg, an original
drawing and never an emoji font's artwork: web/public/favicon.svg (a copy), favicon.ico, the images the web app's
manifest names and the built-in theme's logo (ADR 0044). They are committed, because the Linux build has nothing
to draw them with; a test checks the SVGs. The PNG files' bytes depend on the macOS that drew them, so redraw them
only when heinzel.svg changes.
"""

import hashlib
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "src" / "heinzel" / "modules" / "system" / "notify" / "macos"
APP = SOURCES / "HeinzelNotifier.app"
PACKAGED = "heinzel/modules/system/notify/macos/HeinzelNotifier.app"
"""Where the wheel ships it; the sdist has it under src/."""
TARGETS = ("arm64-apple-macos13", "x86_64-apple-macos13")
ICON_SIZES = (16, 32, 128, 256, 512)
TOOLS = ("swiftc", "lipo", "iconutil", "codesign")
PUBLIC = ROOT / "web" / "public"
WEB_ICONS = {
    "icons/icon-192.png": (192, "tile"),
    "icons/icon-512.png": (512, "tile"),
    "icons/maskable-512.png": (512, "maskable"),
    "icons/apple-touch-icon.png": (180, "maskable"),  # iOS and macOS round it themselves
}
"""The web app's icons by path under web/public: their size and icon.swift's style."""
FAVICON_SIZES = (16, 32)
ARTWORK = SOURCES / "heinzel.svg"
"""Heinzelmännchen's one icon; everything else that shows it is made from this file."""
THEME_LOGO = ROOT / "src" / "heinzel" / "kernel" / "assets" / "theme" / "logo"
"""The built-in theme's logo (ADR 0044): the artwork as its mark, and wordmarks made from it."""
COPIES = (PUBLIC / "favicon.svg", THEME_LOGO / "mark.svg")
"""Byte-for-byte copies of the artwork, for what can't reach it where it is."""
WORDMARKS = {"wordmark-light.svg": ("light", "#1a1a1a"), "wordmark-dark.svg": ("dark", "#ececec")}
"""The built-in theme's wordmarks: the background each is for, and the name's colour on it."""
SOURCE_NAMES = ("Info.plist", "heinzel.svg", "icon.swift", "main.swift")
"""The app's sources, in the order its version digests them."""


def _version() -> str:
    """The digest of the sources, as `heinzel.modules.system.notify._macos.source_version` computes it.

    Standard library only: the build hook runs this in an isolated build environment without hz's dependencies.
    """
    digest = hashlib.sha256()
    for name in SOURCE_NAMES:
        digest.update(name.encode() + b"\0" + (SOURCES / name).read_bytes() + b"\0")
    return digest.hexdigest()[:16]


BUILD_TIMEOUT = 600
"""Seconds one compiler or signing step may take."""


def _run(*command: str) -> None:
    subprocess.run(command, check=True, timeout=BUILD_TIMEOUT)  # noqa: S603 - fixed tools, arguments only


def current() -> bool:
    """Whether the built app is there and was built from these sources."""
    plist = APP / "Contents" / "Info.plist"
    return plist.is_file() and f"<string>{_version()}</string>" in plist.read_text(encoding="utf-8")


def wordmark(background: str, fill: str) -> str:
    """The built-in theme's wordmark: the artwork's shapes, 200 high, and the name beside them.

    Raises:
        ValueError: heinzel.svg isn't one <svg> element with shapes in it.
    """
    without_comments = re.sub(r"<!--.*?-->", "", ARTWORK.read_text(encoding="utf-8"), flags=re.DOTALL)
    match = re.fullmatch(r"\s*<svg\b[^>]*>(.*)</svg>\s*", without_comments, re.DOTALL)
    if match is None or not match.group(1).strip():
        msg = f"{ARTWORK.name} isn't one <svg> element with shapes in it"
        raise ValueError(msg)
    shapes = "".join(f"    {line.strip()}\n" for line in match.group(1).splitlines() if line.strip())
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1120 200" role="img" aria-label="Heinzelmännchen">\n'
        f"  <!-- The built-in theme's wordmark on a {background} background,"
        " made from heinzel.svg by `make icons`. -->\n"
        '  <g transform="scale(0.390625)">\n'
        f"{shapes}"
        "  </g>\n"
        '  <text x="228" y="138" font-family="ui-sans-serif, system-ui, -apple-system, sans-serif" font-size="104"\n'
        f'        font-weight="600" fill="{fill}">Heinzelmännchen</text>\n'
        "</svg>\n"
    )


def _ico(images: list[tuple[int, bytes]]) -> bytes:
    """An .ico file holding PNG images, as every browser reads them."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset, entries, data = 6 + 16 * len(images), b"", b""
    for size, png in images:
        entries += struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    return header + entries + data


MACOS_FOR_SVG = (14, 0)
"""The first macOS whose AppKit reads SVG, which icon.swift needs to draw heinzel.svg."""


def _svg_ready() -> bool:
    """Whether this Mac's AppKit reads SVG."""
    release = tuple(int(part) for part in platform.mac_ver()[0].split(".")[:2] if part.isdigit())
    return release >= MACOS_FOR_SVG


def icons() -> None:
    """Draw the web app's icons into web/public.

    Raises:
        SystemExit: Off macOS 14 or later, or without the Command Line Tools.
    """
    if sys.platform != "darwin" or shutil.which("swiftc") is None or not _svg_ready():
        msg = "The icons are drawn on macOS 14 or later with the Xcode Command Line Tools (xcode-select --install)."
        raise SystemExit(msg)
    with tempfile.TemporaryDirectory(prefix="hz-icons-") as directory:
        work = Path(directory)
        icon = work / "icon"
        _run("swiftc", "-O", "-o", str(icon), str(SOURCES / "icon.swift"))
        for name, (size, style) in WEB_ICONS.items():
            target = PUBLIC / name
            target.parent.mkdir(parents=True, exist_ok=True)
            _run(str(icon), str(ARTWORK), str(target), str(size), style)
        favicons = []
        for size in FAVICON_SIZES:
            out = work / f"favicon-{size}.png"
            _run(str(icon), str(ARTWORK), str(out), str(size))
            favicons.append((size, out.read_bytes()))
        (PUBLIC / "favicon.ico").write_bytes(_ico(favicons))
    for copy in COPIES:
        copy.write_bytes(ARTWORK.read_bytes())
    for name, (background, fill) in WORDMARKS.items():
        (THEME_LOGO / name).write_text(wordmark(background, fill), encoding="utf-8")
    print(
        f"Drew {len(WEB_ICONS)} icons and favicon.ico into {PUBLIC.relative_to(ROOT)}, "
        f"and the theme's logo, from {ARTWORK.name}"
    )


def build() -> None:
    """Build the app beside its sources, replacing an older one.

    Raises:
        SystemExit: Off macOS 14 or later, or without the Command Line Tools.
    """
    if sys.platform != "darwin":
        msg = "Heinzel Notifier builds on macOS only; a wheel built elsewhere uses osascript."
        raise SystemExit(msg)
    if not _svg_ready():
        msg = "Heinzel Notifier builds on macOS 14 or later, whose AppKit draws its icon from heinzel.svg."
        raise SystemExit(msg)
    missing = [tool for tool in TOOLS if shutil.which(tool) is None]
    if missing:
        msg = f"{', '.join(missing)} not found: install the Xcode Command Line Tools (xcode-select --install)"
        raise SystemExit(msg)
    version = _version()
    with tempfile.TemporaryDirectory(prefix="hz-notifier-build-") as directory:
        work = Path(directory)
        app = work / "HeinzelNotifier.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "Resources").mkdir()
        slices = []
        for target in TARGETS:
            out = work / f"notifier-{target}"
            _run("swiftc", "-O", "-target", target, "-o", str(out), str(SOURCES / "main.swift"))
            slices.append(str(out))
        _run("lipo", "-create", "-output", str(app / "Contents" / "MacOS" / "notifier"), *slices)
        icon = work / "icon"
        _run("swiftc", "-O", "-o", str(icon), str(SOURCES / "icon.swift"))
        iconset = work / "heinzel.iconset"
        iconset.mkdir()
        for size in ICON_SIZES:
            _run(str(icon), str(ARTWORK), str(iconset / f"icon_{size}x{size}.png"), str(size), "tile")
            _run(str(icon), str(ARTWORK), str(iconset / f"icon_{size}x{size}@2x.png"), str(size * 2), "tile")
        _run("iconutil", "-c", "icns", "-o", str(app / "Contents" / "Resources" / "heinzel.icns"), str(iconset))
        plist = (SOURCES / "Info.plist").read_text(encoding="utf-8").replace("@VERSION@", version)
        (app / "Contents" / "Info.plist").write_text(plist, encoding="utf-8")
        _run("codesign", "--force", "--sign", "-", "--identifier", "dev.heinzel.notifier", str(app))
        _run("codesign", "--verify", "--strict", str(app))
        shutil.rmtree(APP, ignore_errors=True)
        shutil.copytree(app, APP, symlinks=True)
    print(f"Built {APP.relative_to(ROOT)} ({version})")


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(0 if current() else 1)
    if sys.argv[1:] == ["icons"]:
        icons()
        sys.exit(0)
    build()
