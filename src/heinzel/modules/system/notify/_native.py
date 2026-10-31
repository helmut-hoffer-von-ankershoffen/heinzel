"""Native notifications, for when no web app is open (ADR 0043).

macOS: Heinzel Notifier, hz's own app bundled in the wheel (`_macos.py`): it shows with the Heinzel icon and a click
opens the web app. osascript only as the last resort, when this installation carries no app: it shows as Script
Editor and a click opens Script Editor, so the text names the command to run. Linux: notify-send, when there is a
desktop session.

Text reaches every notifier only as separate arguments, never inside a script or a shell command: osascript runs a
fixed `on run argv` script that reads its strings from `argv`, so a title such as `" & do shell script "…` stays text.
Every string is cleaned of control and invisible characters, flattened to one line, stripped of leading characters
a notifier could read as an option, escaped where notify-send reads markup, and bounded.
"""

import html
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from heinzel import NoticeHelper, clean_text
from heinzel.modules.system.notify._macos import Look, NotifierApp

APP = "Heinzelmännchen"
TITLE_LIMIT = 80
BODY_LIMIT = 240
TIMEOUT = 10
"""Seconds a notifier may take; they answer at once unless something is wrong."""
_SPACE = re.compile(r"\s+")
_LEADING = "-[\\ "
"""Characters a notifier could read as the start of an option."""
_OSASCRIPT = (
    "on run argv",
    "display notification (item 2 of argv) with title (item 1 of argv) subtitle (item 3 of argv)",
    "end run",
)
"""A fixed script: the strings are its arguments, never part of its source."""
_SESSION = ("DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "WAYLAND_DISPLAY")
"""Without one of these there is no desktop session to show a notification in."""
HEINZEL = "heinzel-notifier"
BANNERS = (
    "No banner, only Notification Center? While the screen is shared or mirrored macOS holds them: System Settings > "
    "Notifications > Allow notifications when mirroring or sharing the display; a Focus holds them too; and the "
    "app's alert style must not be None: System Settings > Notifications > {app}."
)
"""The usual reasons a macOS notification is delivered but no banner shows, with where to change them."""
ALLOW = (
    "If no notification appeared: System Settings \u2192 Notifications \u2192 Heinzel Notifier \u2192 "
    "turn on Allow notifications (Banners)."
)
ICON_CACHE = "macOS may show the old icon until you remove Heinzel Notifier there once and run this again."
DENIED_DETAIL = "macOS doesn't allow Heinzel Notifier's notifications"
DENIED_FIX = (
    "System Settings > Notifications > Heinzel Notifier: turn on Allow notifications; then `hz system notify install`"
)
DENIED = f"{DENIED_DETAIL}: {DENIED_FIX}"
NOT_ASKED = (
    "macOS hasn't asked yet whether Heinzel Notifier may show notifications, and asks only someone at the screen: "
    "run `hz system notify install`"
)

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def sanitise(text: str, limit: int) -> str:
    """`text` as one clean line of at most `limit` characters, never starting like an option."""
    line = _SPACE.sub(" ", clean_text(text)).strip().lstrip(_LEADING)
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run a notifier with a timeout; a timeout becomes exit code 124, like timeout(1)."""
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False, timeout=TIMEOUT)  # noqa: S603 - executables resolved with shutil.which, arguments only, no shell
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "", f"{command[0]} timed out after {TIMEOUT}s")


@dataclass(frozen=True, slots=True)
class Native:
    """The native notifier of this machine, or why there is none."""

    name: str | None
    """`heinzel-notifier`, `osascript`, `notify-send`, or None when there is none."""
    executable: str | None = None
    detail: str = ""
    """How notices pop up, in words, for doctor and `hz system notify list`."""
    fix: str = ""
    """What makes it better, or empty."""
    clicks: bool = False
    """Whether a click can open a link."""
    runner: Runner = field(default=run, repr=False, compare=False)
    app: NotifierApp | None = field(default=None, repr=False, compare=False)

    def argv(self, title: str, body: str) -> list[str]:
        """The command that shows one notification with osascript or notify-send; arguments only.

        Raises:
            ValueError: If there is no such notifier.
        """
        if self.name not in {"osascript", "notify-send"} or self.executable is None:
            msg = f"no native notifier: {self.detail}"
            raise ValueError(msg)
        title, body = sanitise(title, TITLE_LIMIT), sanitise(body, BODY_LIMIT)
        if self.name == "osascript":
            return [self.executable, *(arg for line in _OSASCRIPT for arg in ("-e", line)), APP, body or title, title]
        # notify-send: the body may be read as markup by the notification server, the summary isn't.
        return [self.executable, f"--app-name={APP}", "--", title, html.escape(body, quote=False)]

    def post(  # noqa: PLR0913 - what a notification is, all by keyword after the text
        self,
        title: str,
        body: str,
        *,
        url: str | None = None,
        asking: bool = False,
        helper: NoticeHelper | None = None,
        sound: bool = False,
        ident: str | None = None,
        thread: str | None = None,
    ) -> str | None:
        """Show one notification; None when it was shown, else why not.

        `asking` says a person is at the screen (`hz system notify install`): only then may Heinzel Notifier make
        macOS ask whether to allow it. The daemon doesn't, or an unanswered question would hold every tick.

        A `helper` puts its name and reason under the title and, in Heinzel Notifier, its face with its hat colour on
        the notification; other notifiers get the name in front of the title. `ident` is what the notification is
        known by: Heinzel Notifier replaces a notification with the same one, so a repeat never stacks.
        """
        if self.name == HEINZEL and self.app is not None:
            look = Look(
                subtitle=sanitise(f"{helper.name} \u00b7 {helper.reason}", TITLE_LIMIT) if helper else None,
                hat=(helper.hat or "red") if helper else None,  # red: the default hat, so every helper has a face
                sound=sound,
                ident=ident,
                thread=thread,
            )
            return _through_app(self.app, title, body, url=url, asking=asking, look=look)
        if helper is not None:
            title = f"{helper.name}: {title}"
        try:
            command = self.argv(title, body)
        except ValueError as exc:
            return str(exc)
        result = self.runner(command)
        if result.returncode != 0:
            return f"{self.name} failed: {(result.stderr or result.stdout).strip() or f'exit {result.returncode}'}"
        return None


def _through_app(  # noqa: PLR0913 - the app, the text, and how it is shown, all by keyword
    app: NotifierApp, title: str, body: str, *, url: str | None, asking: bool, look: Look | None = None
) -> str | None:
    """Show one notification through Heinzel Notifier, installing it first if needed; None when it was shown."""
    try:
        installed = app.ensure()
    except OSError as exc:
        return f"Heinzel Notifier can't be installed: {exc.strerror or exc}"
    if not app.path.exists():
        return f"Heinzel Notifier isn't installed: {installed.detail}"
    if not asking and app.status() == "not-determined":
        return NOT_ASKED
    said = app.post(sanitise(title, TITLE_LIMIT), sanitise(body, BODY_LIMIT), url, asking=asking, look=look)
    word = said.split(maxsplit=1)[0] if said.split() else ""  # older builds print "posted error=nil"
    if word == "posted":
        return None
    return DENIED if word == "denied" else f"Heinzel Notifier: {said}"


def native(
    platform: str,
    which: Callable[[str], str | None],
    environ: Mapping[str, str],
    runner: Runner = run,
    app: NotifierApp | None = None,
) -> Native:
    """The best native notifier this machine has."""
    if platform == "darwin":
        return _darwin(which, runner, app or NotifierApp())
    if platform.startswith("linux"):
        return _linux(which, environ, runner)
    return Native(None, detail=f"no native notifications on {platform}; `hz system notify list` shows them")


def _darwin(which: Callable[[str], str | None], runner: Runner, app: NotifierApp) -> Native:
    if app.available:
        return Native(
            HEINZEL,
            str(app.path),
            detail="desktop notifications through Heinzel Notifier; a click opens the screen",
            fix=BANNERS.format(app="Heinzel Notifier"),
            clicks=True,
            runner=runner,
            app=app,
        )
    if found := which("osascript"):
        return Native(
            "osascript",
            found,
            detail="desktop notifications through osascript; they show as Script Editor and name the command",
            fix="This installation carries no Heinzel Notifier; a release installed with `uv tool install` does. "
            + BANNERS.format(app="Script Editor"),
            runner=runner,
        )
    return Native(None, detail="osascript isn't on PATH, so the daemon can't pop up notifications")


def _linux(which: Callable[[str], str | None], environ: Mapping[str, str], runner: Runner) -> Native:
    found = which("notify-send")
    if found is None:
        return Native(
            None,
            detail="notify-send isn't on PATH, so the daemon can't pop up notifications",
            fix="install libnotify (notify-send), e.g. `sudo apt install libnotify-bin`",
        )
    if not any(environ.get(name) for name in _SESSION):
        return Native(
            None,
            detail="no desktop session (headless), so the daemon can't pop up notifications; "
            "`hz system notify list` shows them",
        )
    return Native("notify-send", found, detail=f"desktop notifications through {found}", runner=runner)
