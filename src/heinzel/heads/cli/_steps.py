"""The CLI head's view of an operation's named steps: the same `phase` events the web app's dialog shows.

An operation tells its steps through `Phases` (`heinzel.modules.system._phases`): one `phase` event per change,
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
            if state in ENDED:
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
        parts: list[tuple[str, str]] = []
        for step in self._steps.values():
            parts += [("━" * (SEGMENT - 1), STYLES.get(step.state, "")), (" ", "")]
        ended = sum(step.state in ENDED for step in self._steps.values())
        return Text.assemble(*parts, f"{ended}/{len(self._steps)}")

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
        if not self.active:
            if self._live is not None:
                self._live.stop()
                self._live = None
            return
        self._ensure_live().refresh()

    def _renderable(self) -> RenderableType:
        running = next(((i, s) for i, s in enumerate(self._steps.values()) if s.state == "running"), None)
        rows: list[RenderableType] = [self.bar()]
        if running is not None:
            index, step = running
            grid = Table.grid(padding=(0, 1))
            grid.add_row(Spinner("dots", style="cyan"), Text(f"[{index + 1}/{len(self._steps)}] {step.label}"))
            rows.append(grid)
            if self._work is not None:
                description, total, done = self._work
                amount = f" {min(done, total) / total:.0%}" if total else ""
                rows.append(Text(f"    {terminal_safe(description)}{amount}", style="bright_black"))
        return Group(*rows)
