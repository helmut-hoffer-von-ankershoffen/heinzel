"""What `hz app install`, `uninstall` and `doctor` say to a person, in one place.

Every failure is two lines: what happened, in the person's words (app, folder, download, open), and the one command
or action that fixes it. Internal names stay in the exception's details, shown only with `--details` or HZ_DEBUG=1.
Every folder is a full path with its plain name. Design: the CLI install experience note of 2026-11-07.
"""

from dataclasses import dataclass
from pathlib import Path

APP = "Heinzelmännchen"
BANNED_WORDS = ("bundle", "metadata", "receipt", "binding", "ownership", "journal", "seal", "lock")
"""Words a person never sees in the two lines; they belong under --details."""
MAX_LINE = 110


@dataclass(frozen=True)
class Problem:
    """A failure as the person reads it: what happened, then the one thing to do."""

    happened: str
    action: str

    def lines(self) -> list[str]:
        """The two lines, in order."""
        return [self.happened, self.action]


def folder(path: Path, *, everyone: bool) -> str:
    """A folder as a full path plus its plain name; `everyone` for the system Applications folder."""
    return f"{path} (for everyone on this Mac)" if everyone else f"{path} (your Applications folder)"


FORGET_PROMPT = "Forget that install and continue?"
"""Asked in a terminal after the first line of `leftover_install`."""


def leftover_install(applications: Path, *, everyone: bool) -> Problem:
    """An earlier install is recorded, but its app is gone (moved to the Trash by hand)."""
    return Problem(
        f"An earlier install of {APP} is recorded, but the app is gone from {folder(applications, everyone=everyone)}.",
        "Run hz app install --yes, or hz app uninstall, to forget it.",
    )


def forgot_install(backup: Path) -> str:
    """The step row after the old record was set aside."""
    return f"Set aside  old install record ({backup})"


def foreign_copy(app: Path, *, everyone: bool) -> Problem:
    """A copy of the app that hz did not install sits where hz wants to install."""
    return Problem(
        f"There is already a {app.name} in {folder(app.parent, everyone=everyone)} that hz did not install.",
        "Run hz app install --replace to move it to the Trash (nothing is deleted) and install a verified copy.",
    )


def trashed_copy(app: Path, name: str) -> str:
    """The step row after --replace moved a foreign copy to the Trash."""
    return f"Moved      the copy hz did not install from {app.parent} to the Trash as {name}"


def trash_failed(app: Path, *, everyone: bool) -> Problem:
    """--replace could not move the foreign copy."""
    return Problem(
        f"The {app.name} in {folder(app.parent, everyone=everyone)} could not be moved to the Trash.",
        "Move it to the Trash in Finder, then run hz app install again.",
    )


def app_running(applications: Path, *, command: str = "install") -> Problem:
    """The app is open, so it cannot be replaced or removed now."""
    verb = "removed" if command == "uninstall" else "replaced"
    return Problem(
        f"{APP} is open, so the app in {applications} cannot be {verb} right now.",
        f"Quit it, then run hz app {command} again.",
    )


def interrupted(applications: Path, *, everyone: bool) -> Problem:
    """An earlier install stopped halfway and what is on disk no longer matches it."""
    return Problem(
        f"An earlier install into {folder(applications, everyone=everyone)} stopped halfway and cannot be finished "
        "safely.",
        "Run hz app install --details and report what it prints.",
    )


def busy() -> Problem:
    """Another install holds the installer, or a leftover file name is taken."""
    return Problem("Another hz app install seems to be running.", "Wait for it, or run hz app install --details.")


def would_not_open(app: Path) -> Problem:
    """The installed copy's code signature is broken, so macOS would call it damaged; it is not launched."""
    return Problem(
        f"The app at {app} would not open: macOS would say it is damaged.",
        "Run hz app doctor --fix to repair it, then run hz app open again.",
    )


def asks_once() -> str:
    """The installed copy is marked as downloaded, so macOS asks before the first open."""
    return "macOS asks once before it opens this app (Open Anyway in Privacy & Security); hz app doctor explains."


def open_failed(app: Path) -> Problem:
    """MacOS `open` refused the app."""
    return Problem(f"macOS did not open the app at {app}.", "Run hz app doctor to see why.")
