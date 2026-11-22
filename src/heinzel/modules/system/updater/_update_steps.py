"""The checklist of one `hz system update`: its rows, told as the update happens (ADR 0056)."""

from collections.abc import Sequence

from heinzel import Progress
from heinzel._phases import Phases, State
from heinzel.kernel import (
    HeadRefresh,
)
from heinzel.kernel._desktop_app import app_hint
from heinzel.modules.system._models import UpdatePlan, UpdateResult
from heinzel.modules.system.updater._update_rules import SETTLED, plugin_step


def own_rows(names: Sequence[str], plan: UpdatePlan | None) -> list[str]:
    """The plugins with a row of their own before the install says more: those the plan expects to move, or all."""
    if plan is None:
        return list(names)
    moving = {p.distribution for p in plan.plugins if p.action != "current"}
    return [name for name in names if name in moving]


class Steps:
    """The checklist of one `update`, told as it happens (ADR 0056): hz, the plugins, the services, the desktop app.

    Rows start pending, from the plan; a row is real work or a real outcome. Plugins that stay as they are are one
    row. Every event carries `checklist`, so a terminal draws the rows in place and a web app lists them as before.
    """

    def __init__(
        self, progress: Progress, own: Sequence[str], count: int, *, desktop: str | None, daemon: bool
    ) -> None:
        self._own = list(own)
        self._count = count
        self._labels = {
            "check": "hz",
            **{f"plugin:{name}": name for name in own},
            **({"plugins": _plugins_label(count - len(own))} if count > len(own) else {}),
            **({"daemon": "Daemon, web server"} if daemon else {}),
            **({"desktop": desktop} if desktop else {}),
        }
        self._phases = Phases(progress, list(self._labels.items()), operation="system.update", checklist=True)
        self._running: str | None = None

    def _note(self, id_: str, state: State, detail: str | None = None) -> None:
        self._phases.note(id_, self._labels[id_], state, detail)

    def start(self, id_: str) -> None:
        """Run `id_`; the step that ran before it is done (a step this update doesn't show changes nothing)."""
        if id_ not in self._labels or self._running == id_:
            return
        if self._running is not None:
            self._note(self._running, "done")
        self._running = id_
        self._note(id_, "running")

    def fail(self, detail: str) -> None:
        """The running step failed with `detail`; the ones after it never ran."""
        if self._running is not None:
            self._note(self._running, "failed", detail)
            self._running = None

    def installed(self, result: UpdateResult) -> None:
        """Finish hz, then tell what became of each plugin; the ones that stay as they are share one row."""
        moved = result.updated and result.after != result.before
        self._note("check", "done", f"{result.before} → {result.after}" if moved else "newest release")
        self._running = None
        told = {update.distribution: update for update in result.plugins}
        for id_ in [key for key in self._labels if key.startswith("plugin:")]:
            update = told.get(id_.removeprefix("plugin:"))
            if update is None or (update.status == "unchanged" and update.checked and update.pinned is None):
                self._note(id_, "skipped")
                continue
            state, detail = plugin_step(update)
            self._phases.note(id_, self._labels[id_], state, detail, version=update.after or update.before)
        if "plugins" in self._labels:
            rest = [u for u in result.plugins if u.status == "unchanged" and u.checked and u.pinned is None]
            self._labels["plugins"] = _plugins_label(len(rest) or self._count - len(self._own))
            self._note("plugins", "done", "already newest" if result.plugins else "left as they are")

    def services(self, heads: Sequence[HeadRefresh]) -> None:
        """Finish the services' row: done, or a warning that names what finishes it."""
        self._running = None
        if "daemon" not in self._labels:
            return
        shown = [head for head in heads if head.head in {"daemon", "web"} and head.status != "not-installed"]
        bad = next((head for head in shown if head.status not in SETTLED), None)
        if bad is not None:
            self._note("daemon", "warn", bad.detail)
            return
        first = next(iter(shown), None)
        self._note("daemon", "done", None if first is None else _service_words(first))

    def desktop(self, head: HeadRefresh) -> None:
        """Finish the desktop app's row: done, or a warning with what finishes it."""
        self._running = None
        if "desktop" in self._labels:
            self._note("desktop", "done" if head.status == "verified" else "warn", _app_words(head) + app_hint(head))


def _plugins_label(count: int) -> str:
    return f"{count} plugin{'' if count == 1 else 's'}"


def _service_words(head: HeadRefresh) -> str:
    """The services' row when they are in order: restarted into the new version, or already running it."""
    ran = f"run {head.version}" if head.version else "run it"
    return f"restarted, {ran}" if getattr(head, "restarted", None) else ran


def _app_words(head: HeadRefresh) -> str:
    """The desktop app's row: where it went, and whether it was quit and reopened; what finishes it otherwise."""
    before, running = getattr(head, "before", None), getattr(head, "was_running", None)
    if head.status != "verified":
        return head.detail.removesuffix(".")
    if not head.detail.startswith("Updated") and (before == head.version or before is None):
        return "newest, running" if running else "newest"
    moved = f"{before} → {head.version}" if before else f"updated to {head.version}"
    return f"{moved}, quit and reopened" if getattr(head, "reopened", None) else moved
