"""The CLI head's view of an operation's named steps: the same `phase` events the web app's dialog shows.

An operation tells its steps through `Phases` (`heinzel.modules.system.updater._phases`): one `phase` event per change,
`{id, label, state, detail?}`, planned steps first as `pending`. The web app turns them into its checklist; this
turns them into a segmented bar (one segment per step) above the running step on a terminal, a line per step where
output is plain (redirected, `NO_COLOR`, `CI`), and one JSON object per line on stderr for `--format json`, so stdout
keeps exactly the result. Nothing here invents a percentage: a segment is a step that really ran.
"""

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.progress_bar import ProgressBar
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from heinzel.heads.cli._render import TerminalSafe, terminal_safe

SEGMENT = 4
"""Cells of one step's segment in the bar."""
ENDED = frozenset({"done", "warn", "skipped", "failed"})
STYLES = {
    "pending": "bright_black",
    "running": "cyan",
    "done": "green",
    "warn": "yellow",
    "skipped": "bright_black",
    "failed": "red",
}
MARKS = {"done": "✓", "warn": "!", "skipped": "-", "failed": "✗"}
WORDS = {"running": "...", "done": "done", "warn": "warning", "skipped": "skipped", "failed": "failed"}


@dataclass
class Step:
    """One step as the last event told it."""

    label: str
    state: str
    detail: str | None = None


class StepBoard:
    """Shows one operation's steps as they start and end, on the error console.

    `mode` is `live` (a terminal: a bar redrawn in place, finished steps printed above it), `plain` (a line when a
    step starts and when it ends) `json` (each event as one JSON line) or `off`. `close` ends the live display.
    """

    def __init__(self, console: Console, mode: str) -> None:
        """Report on `console` (stderr) in `mode`."""
        self._console = console
        self._mode = mode
        self._steps: dict[str, Step] = {}
        self._lock = threading.Lock()
        self._live: Live | None = None
        self._checklist = False
        """Whether the operation draws one checklist (`checklist` in its events): its rows tick in place."""
        self._work: tuple[str, float | None, float] | None = None
        """What the running step does right now (a download, a wait), with its total and the amount done."""

    @property
    def active(self) -> bool:
        """Whether a step is running, so other progress joins this board instead of drawing its own."""
        return any(step.state == "running" for step in self._steps.values())

    def event(self, data: Mapping[str, object]) -> None:
        """Take one `phase` event: `{id, label, state, detail?}`."""
        id_, label, state = str(data.get("id", "")), str(data.get("label", "")), str(data.get("state", ""))
        detail = data.get("detail")
        if self._mode == "off":
            return
        with self._lock:
            self._checklist = self._checklist or bool(data.get("checklist"))
            before = self._steps.get(id_)
            step = self._steps[id_] = Step(label, state, None if detail is None else str(detail))
            if self._mode == "json":
                line = json.dumps({"event": "phase", **dict(data)}, ensure_ascii=False, default=str)
                self._console.print(line, markup=False, highlight=False, soft_wrap=True)
                return
            if state == "pending" or (before is not None and before.state == state and before.detail == step.detail):
                return
            if state == "running":
                self._work = None
            if self._mode == "plain":
                self._print(self.line(id_))
                return
            if state in ENDED and not self._checklist:
                self._ensure_live().console.print(TerminalSafe(self.line(id_, mark=True)), highlight=False)
            self._refresh()

    def work(self, description: str, total: float | None = None) -> None:
        """The running step does `description` now (of `total`, if known)."""
        with self._lock:
            self._work = (description, total, 0.0)
            self._refresh()

    def advance(self, amount: float) -> None:
        """The running step's current work moved on by `amount`."""
        with self._lock:
            if self._work is not None:
                description, total, done = self._work
                self._work = (description, total, done + amount)
                self._refresh()

    def close(self) -> None:
        """Stop the live display; the finished steps stay printed."""
        with self._lock:
            if self._live is not None:
                self._live.stop()
                self._live = None
            self._steps.clear()
            self._work = None
            self._checklist = False

    def line(self, id_: str, *, mark: bool = False) -> Text:
        """One step's line: `[2/5] Installing the update: done (Updated hz 0.9.31 → 0.9.33)`."""
        ids = list(self._steps)
        step = self._steps[id_]
        count = f"[{ids.index(id_) + 1}/{len(ids)}]"
        detail = f" ({step.detail})" if step.detail and step.state != "running" else ""
        if step.state == "running":
            words = f"{step.label} ..."
        else:
            words = f"{step.label}: {WORDS.get(step.state, step.state)}{detail}"
        if mark:
            return Text.assemble(
                "  ", (MARKS.get(step.state, " "), STYLES.get(step.state, "")), f" {count} ", words.rstrip()
            )
        return Text(f"{count} {words}")

    def bar(self) -> Text:
        """The segmented bar: one segment per step, coloured by how it stands, and how many have ended."""
        steps = list(self._steps.values())  # a snapshot: the live display's thread draws while events arrive
        parts: list[tuple[str, str]] = []
        for step in steps:
            parts += [("━" * (SEGMENT - 1), STYLES.get(step.state, "")), (" ", "")]
        ended = sum(step.state in ENDED for step in steps)
        return Text.assemble(*parts, f"{ended}/{len(steps)}")

    @staticmethod
    def checklist(steps: list[Step], work: tuple[str, float | None, float] | None = None) -> RenderableType:
        """Every row of a checklist: pending, running (a spinner), then ticked in place; skipped rows stay hidden."""
        width = max([27, *(len(step.label) + 2 for step in steps)])
        grid = Table.grid(padding=(0, 1))
        for step in steps:
            if step.state == "skipped":
                continue
            mark: RenderableType = (
                Spinner("dots", style="cyan")
                if step.state == "running"
                else Text(MARKS.get(step.state, "·"), style=STYLES.get(step.state, "bright_black"))
            )
            detail = terminal_safe(step.detail or "") if step.state != "running" else ""
            grid.add_row(
                mark, Text(f"{step.label:<{width}}{detail}", style="" if step.state != "pending" else "bright_black")
            )
        rows: list[RenderableType] = [grid]
        if work is not None:
            description, total, done = work
            amount = f" {min(done, total) / total:.0%}" if total else ""
            rows.append(Text(f"    {terminal_safe(description)}{amount}", style="bright_black"))
        rows.append(StepBoard.overall(steps))
        return Group(*rows)

    @staticmethod
    def overall(steps: list[Step]) -> RenderableType:
        """One horizontal bar under the checklist: steps ended of all, and the name of the step that runs now."""
        counted = [step for step in steps if step.state != "skipped" or step.detail is not None] or steps
        ended = sum(step.state in ENDED for step in counted)
        running = next((step for step in counted if step.state == "running"), None)
        grid = Table.grid(padding=(0, 1))
        grid.add_row(
            ProgressBar(total=max(len(counted), 1), completed=ended, width=30, complete_style="green"),
            Text(f"{ended}/{len(counted)}"),
            Text(terminal_safe(running.label) if running else "", style="cyan"),
        )
        return grid

    def _print(self, text: Text) -> None:
        self._console.print(TerminalSafe(text), highlight=False, soft_wrap=True)

    def _ensure_live(self) -> Live:
        if self._live is None:
            self._live = Live(
                console=self._console, transient=True, refresh_per_second=8, get_renderable=self._renderable
            )
            self._live.start()
        return self._live

    def _refresh(self) -> None:
        if self._mode != "live":
            return
        if not self.active and not (self._checklist and self._live is not None):
            if self._live is not None:
                self._live.stop()
                self._live = None
            return
        self._ensure_live().refresh()

    def _renderable(self) -> RenderableType:
        # Called from the live display's own thread: read snapshots, never the lock (refresh holds it).
        steps, work = list(self._steps.values()), self._work
        if self._checklist:
            return self.checklist(steps, work)
        running = next(((i, s) for i, s in enumerate(steps) if s.state == "running"), None)
        rows: list[RenderableType] = [self.bar()]
        if running is not None:
            index, step = running
            grid = Table.grid(padding=(0, 1))
            grid.add_row(Spinner("dots", style="cyan"), Text(f"[{index + 1}/{len(steps)}] {step.label}"))
            rows.append(grid)
            if work is not None:
                description, total, done = work
                amount = f" {min(done, total) / total:.0%}" if total else ""
                rows.append(Text(f"    {terminal_safe(description)}{amount}", style="bright_black"))
        return Group(*rows)
