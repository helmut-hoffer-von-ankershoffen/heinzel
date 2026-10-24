"""How each way of reaching you stands: the open web app, the native notifier, the daemon (ADR 0043).

`hz system notify test` and the web app's test button show all three, each with what fixes it, so a test that can't
pop up anything says so instead of recording a notice nobody will see.
"""

from dataclasses import dataclass

from heinzel.modules.system.notify._macos import NotifierApp
from heinzel.modules.system.notify._models import Channel, NotifySettings
from heinzel.modules.system.notify._native import DENIED_DETAIL, HEINZEL, Native

INSTALL_NOTIFIER = "hz system notify install"
INSTALL_DAEMON = "hz system daemon install"
SHOWN = "shown"


@dataclass(frozen=True, slots=True)
class DaemonState:
    """What `hz system daemon status` says, as far as notices care."""

    installed: bool
    loaded: bool
    paused: bool


def web_channel(*, watched: bool) -> Channel:
    """The web app pops notices up while it is open; it says so every few seconds."""
    if watched:
        return Channel(name="web", state="ready", detail="a web app is open and pops notices up")
    return Channel(name="web", state="closed", detail="no web app is open")


def native_channel(settings: NotifySettings, notifier: Native, result: str | None) -> Channel:
    """The system's pop-ups: `result` is how a test pop-up just went (`shown` or why not), None when none was tried."""
    if not settings.native:
        return Channel(
            name="native",
            state="off",
            detail="the daemon pops up nothing ([system.notify] native = false)",
            fix="turn-on",
            command="hz system config set system.notify.native true",
        )
    if notifier.name is None:
        return Channel(name="native", state="missing", detail=notifier.detail)
    if result == SHOWN:
        return Channel(name="native", state="shown", detail=f"{notifier.detail}: the test popped up")
    app = notifier.app if notifier.name == HEINZEL else None
    if app is not None:
        return _app_channel(app, result)
    if result is not None:
        return Channel(name="native", state="blocked", detail=result)
    return Channel(name="native", state="ready", detail=notifier.detail)


def _app_channel(app: NotifierApp, result: str | None) -> Channel:
    """Heinzel Notifier: installed, and allowed by macOS."""
    if app.inspect().state == "missing":
        return Channel(
            name="native",
            state="missing",
            detail=result or "Heinzel Notifier isn't installed",
            fix="install-notifier",
            command=INSTALL_NOTIFIER,
        )
    if app.status() == "denied":
        return Channel(name="native", state="blocked", detail=result or DENIED_DETAIL, fix="open-settings")
    if result is not None:
        return Channel(name="native", state="blocked", detail=result, fix="install-notifier", command=INSTALL_NOTIFIER)
    return Channel(name="native", state="ready", detail="Heinzel Notifier is installed and allowed")


def daemon_channel(state: DaemonState | None) -> Channel:
    """The daemon's ticks hand due notices to the native notifier while no web app is open."""
    if state is None:
        return Channel(name="daemon", state="unknown", detail="`hz system daemon status` didn't answer")
    if not state.installed or not state.loaded:
        detail = "it isn't installed" if not state.installed else "it is installed but not loaded"
        return Channel(name="daemon", state="missing", detail=detail, fix="install-daemon", command=INSTALL_DAEMON)
    if state.paused:
        return Channel(
            name="daemon", state="off", detail="it is paused", fix="turn-on", command="hz system daemon resume"
        )
    return Channel(name="daemon", state="ready", detail="it ticks every few minutes")


def pops_up(channels: list[Channel]) -> bool:
    """Whether anything pops the test up: shown now, an open web app, or the daemon with a native notifier that can."""
    states = {channel.name: channel.state for channel in channels}
    if "shown" in states.values() or states.get("web") == "ready":
        return True
    return states.get("daemon") == "ready" and states.get("native") == "ready"
