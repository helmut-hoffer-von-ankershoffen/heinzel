"""The words of the one-command start (`uvx heinzel@latest`): two-line failures and the shared status lines.

An error the person sees is two lines: what happened, then the one command that fixes it. Internal names stay out.

The app's name and the banned-word list come from `_desktop_messages`, which `hz app install` shares; nothing is copied.
"""

from heinzel.heads._desktop_messages import APP, BANNED_WORDS, MAX_LINE

__all__ = ["BANNED_WORDS", "FAILURES", "MAX_LINE"]

APP_QUESTION = "Install the desktop app too? ({location})"
APP_UNSUPPORTED = "The desktop app runs on Apple silicon Macs; the web app opens instead."
APP_INSTALLED = "Installed  {path}"
RUNNING_APP = f"{APP} is running: the desktop app is open. Say hello in the chat; hz start opens it again."
RUNNING_WEB = f"{APP} is running: it opens in your browser. Say hello in the chat."
RUNNING_BACKGROUND = f"{APP} is running. Run hz start to open it."
NOT_STARTED = "Installed hz {version} at {path}. Nothing started.\nNext: hz start (asks), or hz start --yes."

APP_DOWNLOAD_FAILED = (
    f"The desktop app could not be installed, so {APP} opens in your browser.\nRun hz app install later to try again."
)
SERVICE_NOT_STARTED = (
    f"The background service did not start, so {APP} is not running after login yet.\n"
    "Run hz system daemon status (or hz ui status), then hz start again."
)
DOCTOR_PROBLEM = (
    f"{APP} is installed, but hz system doctor reports a problem.\n"
    "Run hz system doctor; hz start opens the app once it is green."
)
INTERRUPTED = "Stopped. What finished stays; hz start continues."
UNVERIFIED = "hz is installed but could not be verified.\nRun hz start --details."

FAILURES = (APP_DOWNLOAD_FAILED, SERVICE_NOT_STARTED, DOCTOR_PROBLEM, INTERRUPTED, UNVERIFIED)
