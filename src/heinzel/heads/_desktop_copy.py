"""Every line `hz app` shows a person: step labels, results and failure headlines, in one place.

The wording is swapped here as a whole (design: research/cli-app-install-design); code passes facts, never text.
Wherever a line names where the app is, it names the full path, never just "Applications".
"""

from pathlib import Path

# Steps, in the order an install runs them: one line each, a bar for the download.
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


def install_into(folder: Path) -> str:
    """The step that moves the app into `folder`."""
    return f"Installing into {folder}"


def remove(bundle: Path) -> str:
    """The step that removes the app at `bundle`."""
    return f"Removing {bundle}"


def verified(bundle: Path, release: str) -> str:
    """The note after the installed app was checked on disk."""
    return f"Verified: {bundle} exists and matches Heinzelmännchen {release}."


# Results.
def installing(version: str) -> str:
    """The first line of an install, before any step."""
    return f"Installing Heinzelmännchen {version} ..."


def installed_at(bundle: Path) -> str:
    """Where the app is now, checked on disk."""
    return f"Heinzel is installed at {bundle}."


def not_moved(bundle: Path, system: Path) -> str:
    """Why an app this user may move to `system` stayed at `bundle` this time."""
    return (
        f"It could not be moved to {system} this time: Heinzelmännchen was running, another "
        f"{bundle.name} is already there, or your home folder is on another volume. Quit the app, move the "
        "other copy to the Trash, then run hz app install again: it moves the app for you."
    )


def not_admin(bundle: Path, system: Path) -> str:
    """Why the app is in the user's own folder at `bundle.parent`."""
    return (
        f"It is in {bundle.parent} because this user cannot write to {system} without an administrator "
        f"password; Finder, Launchpad and Spotlight find it there too. To have it in {system}, make this account "
        "an administrator and run hz app install again: it moves the app for you."
    )


def trust(*, keep_quarantine: bool) -> str:
    """What was checked and how macOS treats the first open."""
    opening = (
        "; it is marked as downloaded, so open it once via System Settings, Privacy & Security, Open Anyway."
        if keep_quarantine
        else ", so it opens with a double-click."
    )
    return (
        "It is an unsigned app from this project, not notarized by Apple. Heinzel verified the download's "
        f"sha256, its build provenance and its code seal{opening}"
    )


def next_step(bundle: Path) -> str:
    """How to open the app, naming its full folder."""
    return f"Next: open Heinzelmännchen from {bundle.parent} in Finder, or press Cmd+Space and type Heinzelmännchen."


def removed(bundle: Path) -> str:
    """After an uninstall."""
    return f"Removed the Heinzelmännchen desktop app from {bundle}; your profile is kept."


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
