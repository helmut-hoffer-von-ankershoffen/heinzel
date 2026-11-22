"""The record that an update promised to install the desktop app when it is quit.

Its own file, not part of `_desktop_app`: `hz app refresh` of a new tree runs inside a published process that already
holds the old `_desktop_app` in memory without these names, so the names must load fresh from the new tree.
"""

import os
import stat
from contextlib import suppress
from pathlib import Path

UPDATE_ON_QUIT = Path(".config/heinzel/desktop-update-on-quit")
"""Under the home folder: an update said the open app installs when it is quit; the app's quit then installs it.

The quit's refresh (`hz app refresh`) otherwise follows the automatic-update setting only, so without this record a
promise made with automatic updates off was never kept.
"""


def promise_update_on_quit(home: Path, version: str) -> None:
    """Record that the open app installs `version` when it is quit; a record that can't be written is skipped."""
    path = home / UPDATE_ON_QUIT
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(version)
    except OSError:
        return


def update_on_quit_promised(home: Path) -> bool:
    """Whether an update promised to install the desktop app when it is quit, and that is not done yet."""
    try:
        return stat.S_ISREG((home / UPDATE_ON_QUIT).lstat().st_mode)
    except OSError:
        return False


def update_on_quit_done(home: Path) -> None:
    """The promised install happened (or nothing is left to install): forget the promise."""
    with suppress(OSError):
        (home / UPDATE_ON_QUIT).unlink()
