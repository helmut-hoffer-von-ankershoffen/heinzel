# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Build Heinzel Notifier, hz's macOS notifier app, into the package: `make notifier` (ADR 0043).

Compiles src/heinzel/modules/system/notify/macos/main.swift for arm64 and x86_64 (macOS 13+), joins them with lipo,
draws the icon with icon.swift and iconutil, writes Info.plist with the sources' digest as CFBundleVersion and signs
the bundle ad hoc (`codesign --sign -`). The result, HeinzelNotifier.app beside the sources, is not in git; the wheel
ships it. Needs macOS with the Xcode Command Line Tools (`xcode-select --install`); elsewhere it says so and exits 1.

`icons` (`make icons`) draws the web app's icons with the same icon.swift into web/public: the images its manifest
names and favicon.ico. They are committed, because the Linux build has nothing to draw them with; the drawing is
original (web/public/favicon.svg is the same shapes), never an emoji font's artwork.
"""

import hashlib
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
    "icons/icon-192.png": (192, False),
    "icons/icon-512.png": (512, False),
    "icons/maskable-512.png": (512, True),
    "icons/apple-touch-icon.png": (180, True),  # iOS and macOS round it themselves
}
FAVICON_SIZES = (16, 32)


def _version() -> str:
    """The digest of the sources, as `heinzel.modules.system.notify._macos.source_version` computes it.

    Standard library only: the build hook runs this in an isolated build environment without hz's dependencies.
    """
    digest = hashlib.sha256()
    for name in ("Info.plist", "icon.swift", "main.swift"):
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


def _ico(images: list[tuple[int, bytes]]) -> bytes:
    """An .ico file holding PNG images, as every browser reads them."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset, entries, data = 6 + 16 * len(images), b"", b""
    for size, png in images:
        entries += struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    return header + entries + data


def icons() -> None:
    """Draw the web app's icons into web/public.

    Raises:
        SystemExit: Off macOS, or without the Command Line Tools.
    """
    if sys.platform != "darwin" or shutil.which("swiftc") is None:
        msg = "The icons are drawn on macOS with the Xcode Command Line Tools (xcode-select --install)."
        raise SystemExit(msg)
    with tempfile.TemporaryDirectory(prefix="hz-icons-") as directory:
        work = Path(directory)
        icon = work / "icon"
        _run("swiftc", "-O", "-o", str(icon), str(SOURCES / "icon.swift"))
        for name, (size, maskable) in WEB_ICONS.items():
            target = PUBLIC / name
            target.parent.mkdir(parents=True, exist_ok=True)
            _run(str(icon), str(target), str(size), *(["maskable"] if maskable else []))
        favicons = []
        for size in FAVICON_SIZES:
            out = work / f"favicon-{size}.png"
            _run(str(icon), str(out), str(size))
            favicons.append((size, out.read_bytes()))
        (PUBLIC / "favicon.ico").write_bytes(_ico(favicons))
    print(f"Drew {len(WEB_ICONS)} icons and favicon.ico into {PUBLIC.relative_to(ROOT)}")


def build() -> None:
    """Build the app beside its sources, replacing an older one.

    Raises:
        SystemExit: Off macOS, or without the Command Line Tools.
    """
    if sys.platform != "darwin":
        msg = "Heinzel Notifier builds on macOS only; a wheel built elsewhere uses osascript."
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
            _run(str(icon), str(iconset / f"icon_{size}x{size}.png"), str(size))
            _run(str(icon), str(iconset / f"icon_{size}x{size}@2x.png"), str(size * 2))
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
