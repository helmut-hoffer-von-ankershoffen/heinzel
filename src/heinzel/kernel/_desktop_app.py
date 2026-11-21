"""Which Heinzel desktop app sits in Applications: the native app or the Electron app, told by its bundle id.

Both variants install as `Heinzelmaennchen.app` in the same folder, so the name says nothing: only the bundle id in
`Contents/Info.plist` does. The installer, the updater, `hz --version` and the about API all read it here.
"""

import plistlib
import stat
import sys
from pathlib import Path
from typing import Literal, cast
from xml.parsers.expat import ExpatError

from pydantic import BaseModel, Field

type DesktopVariant = Literal["native", "electron"]
BUNDLE_NAME = "Heinzelmaennchen.app"
NATIVE_ID = "team.heinzel.apple"
ELECTRON_ID = "org.heinzelmaennchen.desktop"
VARIANTS: dict[str, DesktopVariant] = {NATIVE_ID: "native", ELECTRON_ID: "electron"}
EXECUTABLES: dict[DesktopVariant, str] = {"native": "Heinzel", "electron": "Heinzelmaennchen"}
LABELS: dict[DesktopVariant, str] = {"native": "Native app", "electron": "Electron app"}
SYSTEM_APPLICATIONS = Path("/Applications")
_MAX_PLIST_BYTES = 64 * 1024


class DesktopApp(BaseModel):
    """The desktop app bundle found in Applications, whoever installed it."""

    path: str = Field(description="Where the bundle is.")
    variant: DesktopVariant | None = Field(
        description="native (bundle id team.heinzel.apple) or electron (org.heinzelmaennchen.desktop); none if it "
        "is neither."
    )
    bundle_id: str | None = Field(description="Its CFBundleIdentifier; none if it can't be read.")
    version: str | None = Field(description="Its CFBundleShortVersionString; none if it can't be read.")
    executable: str | None = Field(default=None, description="Its CFBundleExecutable; none if it can't be read.")
    symlink: bool = Field(default=False, description="Whether the path is a symbolic link, not the bundle itself.")

    @property
    def label(self) -> str:
        """`Native app 0.9.53`, `Electron app 0.9.53`, or what is known about another bundle."""
        name = LABELS[self.variant] if self.variant else "Desktop app"
        return f"{name} {self.version}" if self.version else name


def label(variant: DesktopVariant | None) -> str:
    """The variant in plain words."""
    return LABELS[variant] if variant else "Desktop app"


def read_bundle(bundle: Path) -> DesktopApp | None:
    """Read the bundle's identity from its Info.plist without following a symlink; None if nothing is there."""
    try:
        info = bundle.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(info.st_mode):
        return DesktopApp(path=str(bundle), variant=None, bundle_id=None, version=None, symlink=True)
    values: dict[str, object] = {}
    metadata = bundle / "Contents" / "Info.plist"
    try:
        plist = metadata.lstat()
        if stat.S_ISREG(plist.st_mode) and plist.st_size <= _MAX_PLIST_BYTES:
            loaded = plistlib.loads(metadata.read_bytes())
            values = cast("dict[str, object]", loaded) if isinstance(loaded, dict) else {}
    except (OSError, ValueError, ExpatError):
        values = {}
    found = {key: value if isinstance(value, str) else None for key, value in values.items()}
    bundle_id = found.get("CFBundleIdentifier")
    return DesktopApp(
        path=str(bundle),
        variant=VARIANTS.get(bundle_id or ""),
        bundle_id=bundle_id,
        version=found.get("CFBundleShortVersionString"),
        executable=found.get("CFBundleExecutable"),
    )


def installed_desktop_app(home: Path, system: Path | None = None) -> DesktopApp | None:
    """The bundle in /Applications (or `system`), else in ~/Applications; None when neither holds one."""
    for folder in dict.fromkeys((system or SYSTEM_APPLICATIONS, home / "Applications")):
        if (found := read_bundle(folder / BUNDLE_NAME)) is not None:
            return found
    return None


SWITCH_TO_NATIVE = "`hz app install --flavor native` switches to the Native app"
"""Said under an Electron app on a Mac: an update keeps it Electron, this command switches."""


def app_hint(head: object) -> str:
    """What an Electron app's update row adds: how to switch to the native app; nothing for any other."""
    return f"; {SWITCH_TO_NATIVE}" if getattr(head, "variant", None) == "electron" else ""


def desktop_app_here() -> DesktopApp | None:
    """The desktop app on this Mac, for `hz --version` and the about API; None elsewhere or when there is none."""
    if sys.platform != "darwin":
        return None
    try:
        return installed_desktop_app(Path.home())
    except (OSError, RuntimeError):
        return None
