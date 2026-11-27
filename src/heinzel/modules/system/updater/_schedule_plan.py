"""The update plan as data: what was found, the countdown, postponements, failures and the status the app shows."""

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, Field

ACCEPTANCE_EVENT = "web.auto_update.acceptance"
"""Once per check of a newer hz old enough to install: whether its release was accepted (ADR 0138)."""

CHECK_EVERY = timedelta(minutes=30)
"""How often the server asks for newer releases."""
RECHECK_GAP = timedelta(seconds=10)
"""The least time between two looks that people ask for."""
JITTER = timedelta(minutes=5)
"""At most this much is added to each check at random, so many installations don't ask at the same second."""
FIRST_CHECK = (timedelta(minutes=1), timedelta(minutes=5))
"""The first check comes this long after start, at random within the range."""
COUNTDOWN = timedelta(seconds=30)
"""The warning every window gets before an update applies."""
POSTPONE = timedelta(hours=1)
"""How far one click on Postpone moves the apply time."""
RETRY_AFTER_FAILURE = timedelta(hours=6)
"""The earliest next attempt after a failed one."""
TIMEOUT = 10.0
"""Seconds a release source may take per network operation."""
STEP_SECONDS = 1.0
"""How often the server looks at its plan."""
RELOAD_RETRY = timedelta(minutes=1)
"""How soon a server that runs an older hz than is installed looks again, when work held off its restart."""
FILENAME = "auto-update.json"

UPDATE_MODULE, UPDATE_OPERATION, RECOVER_OPERATION = "system", "apply-announced", "recover-announced"
REPAIR_OPERATION = ("system.plugin", "repair")
"""Installs the plugins an installed plugin requires but lacks; the updater asks it when nothing else is due."""

type Phase = Literal["off", "idle", "waiting", "countdown", "applying"]


@dataclass
class Plan:
    """What is pending and when it applies; pure data, changed only by the functions below."""

    core: str | None = None
    """The newer hz release, if any."""
    plugins: dict[str, str] = field(default_factory=dict[str, str])
    """Plugins with a newer release, by distribution, mapped to it."""
    window_at: datetime | None = None
    """The earliest time the countdown may start; None when nothing is pending."""
    apply_at: datetime | None = None
    """When the running countdown ends and the update applies; None when no countdown runs."""
    postponements: int = 0
    retry_at: datetime | None = None
    """No attempt before this, after a failure."""
    message: str | None = None
    """One plain message for the person: an update that worked or failed."""
    applying: bool = False
    """The update runs now."""
    restarting_to: str | None = None
    """The version a restart in place is expected to load, saved before restarting."""
    commit: str | None = None
    """The commit the announced hz release's tag named when it was announced; checked once it is installed."""
    refused: dict[str, Any] | None = None
    """News that was installed but didn't start, and was put back: never applied again; newer news is."""
    failed_on: str | None = None
    """The hz version that was running when `message` reported a failure; another version makes it history."""
    plugin_commits: dict[str, str] = field(default_factory=dict[str, str])
    """For announced plugins pinned to a GitHub commit: the commit their release tag named when announced."""
    followed: list[str] = field(default_factory=list[str])
    """Plugins pinned to a GitHub commit whose release tags the last check could read: not updated by hand."""
    held_plugins: list[str] = field(default_factory=list[str])
    """One line per plugin release the last check held back, because another plugin can't move with it."""
    core_held: str | None = None
    """A newer hz release the last check held back, because its release is not accepted (yet)."""

    @property
    def pending(self) -> bool:
        """Whether anything newer is waiting to be installed."""
        return self.core is not None or bool(self.plugins)

    def to_json(self) -> dict[str, Any]:
        """The plan as JSON for the state file."""

        def stamp(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "core": self.core,
            "plugins": self.plugins,
            "window_at": stamp(self.window_at),
            "apply_at": stamp(self.apply_at),
            "postponements": self.postponements,
            "retry_at": stamp(self.retry_at),
            "message": self.message,
            "restarting_to": self.restarting_to,
            "refused": self.refused,
            "commit": self.commit,
            "failed_on": self.failed_on,
            "plugin_commits": self.plugin_commits,
            "followed": self.followed,
            "held_plugins": self.held_plugins,
            "core_held": self.core_held,
        }

    @classmethod
    def from_json(cls, data: object, *, fresh: bool = False) -> "Plan":
        """A plan from the state file; anything unreadable starts afresh; `fresh` at a server's start."""
        if not isinstance(data, dict):
            return cls()
        values: dict[str, Any] = dict(data)  # pyright: ignore[reportUnknownArgumentType] - free-form JSON

        def when(name: str) -> datetime | None:
            value = values.get(name)
            try:
                parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
            except ValueError:
                return None
            return parsed if parsed is None or parsed.tzinfo else parsed.replace(tzinfo=UTC)

        def text(name: str) -> str | None:
            value = values.get(name)
            return value if isinstance(value, str) else None

        found_plugins: object = values.get("plugins")
        plugins = (
            {str(k): str(v) for k, v in found_plugins.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            if isinstance(found_plugins, dict)
            else {}
        )

        def texts(name: str) -> list[str]:
            value: object = values.get(name)
            return [str(item) for item in cast("list[object]", value)] if isinstance(value, list) else []

        found_commits: object = values.get("plugin_commits")
        commits = (
            {str(k): str(v) for k, v in cast("dict[object, object]", found_commits).items()}
            if isinstance(found_commits, dict)
            else {}
        )
        count = values.get("postponements")
        return cls(
            core=text("core"),
            plugins=plugins,
            window_at=when("window_at"),
            # A countdown never survives a restart: the windows must see it again. Another server's countdown does.
            apply_at=None if fresh else when("apply_at"),
            postponements=count if isinstance(count, int) and count >= 0 else 0,
            retry_at=when("retry_at"),
            message=text("message"),
            restarting_to=text("restarting_to"),
            refused=_news(values.get("refused")),
            commit=text("commit"),
            failed_on=text("failed_on"),
            plugin_commits=commits,
            followed=texts("followed"),
            held_plugins=texts("held_plugins"),
            core_held=text("core_held"),
        )


def _news(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    news = cast("dict[str, Any]", value)
    plugins: object = news.get("plugins")
    core: object = news.get("core")
    return {
        "core": core if isinstance(core, str) else None,
        "plugins": {str(k): str(v) for k, v in cast("dict[object, object]", plugins).items()}
        if isinstance(plugins, dict)
        else {},
    }


def found(plan: Plan, core: str | None, plugins: dict[str, str], now: datetime) -> None:
    """Record what a check found. The same news keeps its times; new news waits for its own window.

    News that was put back after it didn't start counts as no news, so the same broken release is never retried.
    """
    if plan.refused == {"core": core, "plugins": dict(plugins)}:
        core, plugins = None, {}
    if core == plan.core and plugins == plan.plugins:
        return
    first_news = not plan.pending
    plan.core, plan.plugins = core, dict(plugins)
    if core is None and not plugins:
        plan.window_at = plan.apply_at = None
        plan.postponements = 0
        return
    if first_news:
        plan.window_at = max(now, plan.retry_at) if plan.retry_at else now
        plan.apply_at = None
        plan.postponements = 0


def step(plan: Plan, now: datetime, *, busy: bool) -> bool:
    """Advance the plan to `now`; True when the update should apply now.

    The countdown starts only when nothing is busy, and work that starts during it cancels it until all is idle again,
    when a fresh countdown warns every window once more.
    """
    if not plan.pending or plan.applying or plan.window_at is None:
        return False
    if plan.retry_at is not None and now < plan.retry_at:
        plan.apply_at = None
        return False
    if plan.apply_at is None:
        if now >= plan.window_at and not busy:
            plan.apply_at = now + COUNTDOWN
        return False
    if busy:
        plan.apply_at = None
        plan.window_at = now
        return False
    return now >= plan.apply_at


def postpone(plan: Plan, now: datetime) -> None:
    """Move the apply time one hour later; the countdown comes back 30 seconds before it."""
    if not plan.pending or plan.applying:
        return
    due = plan.apply_at or max(now + COUNTDOWN, (plan.window_at or now) + COUNTDOWN)
    plan.apply_at = None
    plan.window_at = due + POSTPONE - COUNTDOWN
    plan.postponements += 1


def failed(plan: Plan, message: str, now: datetime, running: str | None = None) -> None:
    """Keep the news, wait at least six hours, and say what happened once, naming the version still `running`."""
    plan.failed_on = running
    plan.applying = False
    plan.apply_at = None
    plan.restarting_to = None
    plan.retry_at = now + RETRY_AFTER_FAILURE
    plan.window_at = plan.retry_at
    plan.message = message


def done(plan: Plan, message: str) -> None:
    """Forget what was pending after an update that worked, keeping the message that says so and what is followed."""
    plan.__dict__.update(Plan(message=message, followed=list(plan.followed)).__dict__)


def settle(plan: Plan, running: str, now: datetime) -> None:
    """At a server's start: forget what the installation already has, and a failure another version reported.

    hz may have been updated by other means since (`hz system update`, the footer, another server): news of a release
    that is not newer than `running` is done, and its failure, or one reported while another version ran, is history.
    A failure without a recorded version is history once its news is, or no news is left: older state files didn't
    record one.
    """
    if plan.applying or plan.restarting_to is not None:
        return
    done_news = plan.core is not None and not_newer(plan.core, running)
    if done_news:
        plan.core, plan.commit = None, None
        if not plan.plugins:
            plan.window_at = plan.apply_at = None
            plan.postponements = 0
    stale = plan.failed_on is not None and plan.failed_on != running
    legacy = plan.failed_on is None and (done_news or not plan.pending)  # older files; no news left to retry
    if plan.retry_at is not None and (stale or legacy):
        plan.retry_at = plan.message = plan.failed_on = None  # only a failure sets `retry_at`, with its message
        if plan.pending:  # other news, e.g. a plugin, waits only for its own window again
            plan.window_at = now
    elif stale:
        plan.failed_on = None


def not_newer(release: str, running: str) -> bool:
    try:
        return Version(release) <= Version(running)
    except InvalidVersion:
        return False


def failure_message(running: str) -> str:
    """The one plain message after a failed attempt."""
    return (
        f"The automatic update didn't work, so hz {running} keeps running as before. It will try again in 6 hours; "
        "to update now, use Update in the footer."
    )


class PluginHold(BaseModel):
    """One installed plugin that automatic updates leave alone, and why, in the words the Updates pane needs."""

    name: str = Field(description="The plugin's distribution name.")
    why: Literal["pinned", "source"] = Field(
        description="pinned: someone holds it to one version; source: it is installed from git, a folder or a URL."
    )
    version: str | None = Field(default=None, description="The version a pinned plugin is held to.")
    how: Literal["git", "folder", "link"] | None = Field(
        default=None, description="Where a source plugin comes from: a git repository, a folder or a URL."
    )


class AutoUpdateStatus(BaseModel):
    """What every window shows about automatic updates; the server's plan, never a browser's."""

    enabled: bool = Field(description="Whether automatic updates are on and can work here.")
    reason: str | None = Field(description="Why they can't work here, in plain words; none when they can.")
    phase: Phase = Field(description="off, idle (nothing newer), waiting, countdown or applying.")
    version: str = Field(description="The hz version this server loaded.")
    core: str | None = Field(description="The newer hz release that will be installed, if any.")
    plugins: dict[str, str] = Field(description="Plugins that will be updated, mapped to their new version.")
    seconds_left: int | None = Field(description="Seconds until the update applies, while the countdown runs.")
    apply_at: datetime | None = Field(description="When the update applies, while the countdown runs.")
    next_window_at: datetime | None = Field(description="When the next countdown may start, while waiting.")
    postponements: int = Field(description="How often the update has been put off.")
    message: str | None = Field(description="One plain message about the last automatic update.")
    development: str | None = Field(
        default=None,
        description="Why this hz runs for development, so it never updates itself and both switches are disabled.",
    )
    by_hand: list[str] = Field(
        default_factory=list[str],
        description="Installed plugins from git, a folder or a URL, which only a person updates; not those pinned to "
        "a GitHub commit whose repository tags their releases.",
    )
    plugins_held: list[str] = Field(
        default_factory=list[str],
        description="One line per plugin release held back because another plugin can't move with it.",
    )
    core_held: str | None = Field(
        default=None,
        description="A newer hz release automatic updates hold back because it is not accepted yet; installing it by "
        "hand (`hz system update`) still works.",
    )
    holds: list[PluginHold] = Field(
        default_factory=list[PluginHold],
        description="Every installed plugin automatic updates leave alone, with the reason; by_hand lists the "
        "source installs among them.",
    )
    checked_at: datetime | None = Field(
        default=None, description="When this server last looked for hz and plugin news; None before its first look."
    )
    core_checked: bool | None = Field(
        default=None, description="Whether the last look at hz releases got an answer; None before the first look."
    )
    plugins_checked: bool | None = Field(
        default=None, description="Whether the last look at plugin releases got an answer; None before the first look."
    )
    held: str | None = Field(
        default=None,
        description="The one line both switches show, disabled, when the setting can't turn updates on here: "
        "development, or update checks off.",
    )
    error: str | None = Field(
        default=None,
        description="Why the last look for news or the last automatic update failed, in the words the failure gave; "
        "None when the last look and the last update went well.",
    )


def plan_phase(plan: Plan, reason: str | None) -> Phase:
    """The phase every window shows for `plan`."""
    if reason is not None:
        return "off"
    if plan.applying or plan.restarting_to:
        return "applying"
    if plan.apply_at is not None:
        return "countdown"
    return "waiting" if plan.pending else "idle"


class Activity:
    """Requests that change state and are still running in this server: a chat answer, an install."""

    def __init__(self) -> None:
        """Nothing running yet."""
        self._lock = threading.Lock()
        self._running: dict[str, int] = {}

    def enter(self, what: str = "a request") -> None:
        """One more request is running; `what` names it (`POST /api/...`) for the log."""
        with self._lock:
            self._running[what] = self._running.get(what, 0) + 1

    def leave(self, what: str = "a request") -> None:
        """One request finished."""
        with self._lock:
            count = self._running.get(what, 0) - 1
            if count > 0:
                self._running[what] = count
            else:
                self._running.pop(what, None)

    @property
    def busy(self) -> bool:
        """Whether any is running."""
        with self._lock:
            return bool(self._running)

    def running(self) -> list[str]:
        """What is running, by name, for the log."""
        with self._lock:
            return sorted(self._running)
