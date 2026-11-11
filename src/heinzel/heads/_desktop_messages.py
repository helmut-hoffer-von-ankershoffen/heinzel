"""What `hz app install`, `uninstall` and `doctor` say to a person, in one place.

Every failure is two lines: what happened, in the person's words (app, folder, download, open), and the one command
or action that fixes it. Internal names stay in the exception's details, shown only with `--details` or HZ_DEBUG=1.
Every folder is a full path with its plain name. Progress is step labels the installer reports, folded into
labelled rows by `install_rows`; code passes facts, never text. Design: the CLI install experience note of
2026-11-07.
"""

import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from heinzel.heads._desktop_archive import BUNDLE_NAME
from heinzel.heads._desktop_report import Row

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


def plain_name(*, everyone: bool) -> str:
    """The plain name of an Applications folder; `everyone` for the system one."""
    return "for everyone on this Mac" if everyone else "your Applications folder"


def folder(path: Path, *, everyone: bool) -> str:
    """A folder as a full path plus its plain name; `everyone` for the system Applications folder."""
    return f"{path} ({plain_name(everyone=everyone)})"


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


def untrusted_record(path: Path) -> Problem:
    """A file hz keeps about its install has the wrong owner, permissions or size, so hz does not trust it."""
    return Problem(
        f"hz does not trust its install notes at {path}: wrong owner, permissions or size.",
        "Move that file to the Trash, then run hz app install again.",
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


def lost() -> Problem:
    """The install reported success, but the app is not where hz recorded it."""
    return Problem(f"{APP} was installed but cannot be found.", "Run hz app doctor.")


INSTALLING_FIRST = "Not installed yet, installing…"
"""What hz app open says before it installs the app."""


def opened(app: Path) -> str:
    """After hz app open."""
    return f"Opened {app}"


def open_failed(app: Path) -> Problem:
    """MacOS `open` refused the app."""
    return Problem(f"macOS did not open the app at {app}.", "Run hz app doctor to see why.")


# Steps, in the order an install runs them. They are what the installer reports; the CLI folds them into the rows
# below (`install_rows`), so a person sees one Downloading bar and four check-marked rows.
RESOLVE = "Resolving release {tag}"
FETCH_MANIFEST = "Downloading release manifest and provenance"
VERIFY_MANIFEST = "Verifying manifest provenance (GitHub attestation)"
CHECK_PRODUCER = "Checking the release came from the protected build"
DOWNLOAD = "Downloading the desktop app ({megabytes:.0f} MB)"
VERIFY_CHECKSUM = "Verifying checksum"
VERIFY_APP = "Verifying app provenance (GitHub attestation)"
CONFIRM_ASSETS = "Confirming the release assets did not change"
CHECK_RUNNING = "Checking whether the app is running"
EXTRACT = "Extracting the app"
CHECK_BUNDLE = "Checking the app bundle (identifier, version, Apple silicon)"
CHECK_SEAL = "Checking the app's code seal"
WRITE_RECEIPT = "Writing the install receipt"
VERIFY_INSTALLED = "Verifying the installed app"
WAIT_FOR_LOCK = "Waiting for another Heinzelmännchen install, update or removal to finish..."
_INSTALL_INTO = "Installing into "


def install_into(folder: Path) -> str:
    """The step that moves the app into `folder`."""
    return f"{_INSTALL_INTO}{folder}"


def remove(bundle: Path) -> str:
    """The step that removes the app at `bundle`."""
    return f"Removing {bundle}"


# Rows: a 10-character past-tense label, one fact each.
DOWNLOADING = "Downloading"
VERIFIED = "checksum and build provenance match the GitHub release"
SEALED = "local code seal; no quarantine, so it opens with a double-click"
SEALED_KEEP_QUARANTINE = "local code seal; marked as downloaded, so macOS asks once (Privacy & Security, Open Anyway)"
RECORDED = "hz owns this copy; hz app doctor checks it, hz app uninstall removes it"


def _template(step: str) -> str:
    return step.split("{", 1)[0]


_DOWNLOAD_STEPS = tuple(
    _template(step) for step in (RESOLVE, FETCH_MANIFEST, VERIFY_MANIFEST, CHECK_PRODUCER, DOWNLOAD)
)
_VERIFY_STEPS = (VERIFY_CHECKSUM, VERIFY_APP, CONFIRM_ASSETS, EXTRACT, CHECK_BUNDLE)


def install_rows(*, keep_quarantine: bool) -> Callable[[str], Row | None]:
    """Which row each install step belongs to; steps of no row (the running check) show nothing.

    Returns:
        A function from the installer's step description to its row.
    """
    verified = Row("Verified", VERIFIED)
    rows = {
        **dict.fromkeys(_VERIFY_STEPS, verified),
        CHECK_SEAL: Row("Sealed", SEALED_KEEP_QUARANTINE if keep_quarantine else SEALED),
        WRITE_RECEIPT: Row("Installed"),
        VERIFY_INSTALLED: Row("Recorded", RECORDED),
    }

    started = False

    def row(step: str) -> Row | None:
        nonlocal started
        if step.startswith(_DOWNLOAD_STEPS):
            found: Row | None = Row(DOWNLOADING, download=True)
        elif step.startswith(_INSTALL_INTO):
            found = Row("Installed", str(Path(step.removeprefix(_INSTALL_INTO)) / BUNDLE_NAME))
        else:
            found = rows.get(step)
        if step == VERIFY_INSTALLED and not started:
            return None  # an install with nothing to do only checks the app it has: no rows
        started = started or found is not None
        return found

    return row


# Results.
def place(folder: Path, system: Path) -> str:
    """The plain name of an Applications folder."""
    return plain_name(everyone=folder == system)


def _why(folder: Path, system: Path, *, admin: bool) -> str:
    if folder == system:
        return f"{place(folder, system)}; you are an administrator" if admin else place(folder, system)
    if admin:
        return place(folder, system)
    return f"{place(folder, system)}; this account is not an administrator, so {system} would need a password"


def installing(version: str, previous: str | None, folder: Path, system: Path, *, admin: bool) -> str:
    """The first line of an install: what, where (full path and plain name) and why there."""
    why = _why(folder, system, admin=admin)
    if previous is None:
        return f"Installing Heinzelmännchen {version} into {folder} ({why})"
    if previous == version:
        return f"Reinstalling Heinzelmännchen {version} in {folder} ({why})"
    return f"Updating Heinzelmännchen {previous} → {version} in {folder} ({why})"


def current(version: str, bundle: Path) -> str:
    """The only line when the app is already this version."""
    return f"Heinzelmännchen {version} is already installed at {bundle}. Nothing to do."


def not_moved(bundle: Path, system: Path) -> str:
    """Why an app this user may move to `system` stayed at `bundle` this time."""
    return (
        f"It stayed in {bundle.parent} ({place(bundle.parent, system)}): Heinzelmännchen was open, or another "
        f"{bundle.name} is in {system}. Quit it, then run hz app install again."
    )


def done(bundle: Path) -> str:
    """The last line of an install: how to open the app."""
    return f"Done. Open it from Launchpad, or run: open {shlex.quote(str(bundle))}"


def removed(bundle: Path) -> str:
    """After an uninstall, after the check mark."""
    return f"Removed {bundle}"


PROFILE_KEPT = "Your Heinzel profile and settings are kept."


def doctor_app(bundle: Path, system: Path) -> str:
    """Doctor's first line: the app's full path and its folder's plain name."""
    return f"App: {bundle} ({place(bundle.parent, system)}; installed by hz)"


NOT_INSTALLED = "The Heinzelmännchen desktop app is not installed."
NOT_INSTALLED_FOR_DOCTOR = "The Heinzel desktop app is not installed. Run hz app install."
LOST = "The desktop app was installed but cannot be found. Run hz app doctor."
DEFERRED = "Quit the Heinzel desktop app, then run hz app install again."
DOCTOR_FIX = "Run hz app doctor --fix to repair it."

# Failures: a headline; the detail comes from the step that failed.
CANCELLED_INSTALL = "Cancelled. Nothing was installed; run hz app install to try again."
CANCELLED_UNINSTALL = "Cancelled. The app was left as it was."
INSTALL_FAILED = "Desktop installation failed."
UNINSTALL_FAILED = "Desktop uninstall failed."
CHECK_FAILED = "Desktop check failed."
UPDATE_FAILED = "Desktop update failed."
