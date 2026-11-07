"""The CLI head's progress: Rich spinners and bars on stderr, silent when the output is JSON."""

import os
import sys
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from datetime import timedelta

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.progress import Progress as RichProgress
from rich.prompt import Confirm
from rich.text import Text

from heinzel import HeinzelError
from heinzel.heads._desktop_report import Advance as ByteAdvance
from heinzel.heads._desktop_report import Row
from heinzel.heads.cli import _playful
from heinzel.heads.cli._render import TerminalSafe, terminal_safe
from heinzel.kernel import Emit, Progress
from heinzel.kernel._progress import Advance


class ConsoleProgress(Progress):
    """Shows progress on the error console, so stdout stays clean for results."""

    def __init__(self, console: Console, *, playful: bool = False) -> None:
        """Report on `console` (stderr); with `playful`, a Heinzelmännchen walks instead of the spinner."""
        self._console = console
        self._playful = playful
        self.enabled = True
        self.listener: Emit | None = None
        """Who gets the events operations stream (`hz chat` prints the answer as it comes); none drops them."""

    def emitter(self) -> Emit:
        """Events go to the listener set when they happen; dropped while there is none."""

        def emit(kind: str, data: Mapping[str, object]) -> None:
            if self.listener is not None:
                self.listener(kind, data)

        return emit

    @contextmanager
    def task(self, description: str, total: float | None = None) -> Generator[Advance]:
        """A spinner, or a bar when `total` is known; removed when the work is done.

        Yields:
            A function to call with the amount of work done since the last call.
        """
        if not self.enabled:
            yield lambda _amount: None
            return
        columns: list[ProgressColumn] = [
            WalkColumn() if self._playful else SpinnerColumn(),
            TextColumn("{task.description}"),
        ]
        if total is not None:
            columns += [BarColumn(), TaskProgressColumn()]
        with RichProgress(*columns, TimeElapsedColumn(), console=self._console, transient=True) as progress:
            task_id = progress.add_task(description, total=total)
            yield lambda amount: progress.advance(task_id, amount)


class WalkColumn(ProgressColumn):
    """A Heinzelmännchen walking back and forth where the spinner would be."""

    def render(self, task: Task) -> Text:
        """The frame for the task's elapsed time."""
        return _playful.walking(task.get_time())


def plain_output(console: Console, environ: Mapping[str, str] | None = None) -> bool:
    """Whether `console` gets no art, bar or colour: redirected, `NO_COLOR` or `CI`."""
    environ = os.environ if environ is None else environ
    return not console.is_terminal or console.no_color or bool(environ.get("NO_COLOR")) or bool(environ.get("CI"))


def _ignore(amount: int) -> None:
    del amount


def _no_rows(step: str) -> Row | None:
    del step
    return None


class StepReporter:
    """Folds an operation's steps into labelled rows on the error console, so stdout keeps only the result.

    `rows` maps each step to its row; steps of no row show nothing. A row is printed with a check mark (`ok` when
    plain) once the next row starts or `close` says the work is complete; the download row shows a bar, its size and
    the elapsed time. `header`, if any, is printed before the first row.
    """

    def __init__(
        self, console: Console, *, header: str | None = None, rows: Callable[[str], Row | None] | None = None
    ) -> None:
        """Report on `console` (stderr)."""
        self._console = console
        self._plain = plain_output(console)
        self._live = not self._plain and console.is_interactive
        self._header = header
        self._rows = rows or _no_rows
        self._row: Row | None = None
        self._started = 0.0
        self._done = 0
        self._total: int | None = None
        self._partial = False
        """Plain output only: the download line is printed up to here and still waits for its end."""
        self._progress: RichProgress | None = None
        self._task: TaskID | None = None

    def _print(self, text: Text | str) -> None:
        line = text if isinstance(text, Text) else Text(text)
        self._console.print(TerminalSafe(line), highlight=False, soft_wrap=True)

    def _print_on(self, text: str) -> None:
        """Continue the plain download line without ending it."""
        self._console.print(terminal_safe(text), markup=False, highlight=False, soft_wrap=True, end="")

    def _end_partial(self) -> None:
        if self._partial:
            self._console.print()
            self._partial = False

    def mark(self) -> Text:
        """The check mark, or `ok` where output is plain."""
        return Text("ok") if self._plain else Text("✓", style="green")

    def check(self, text: str) -> None:
        """Print one line that starts with the check mark."""
        self._print(Text.assemble(self.mark(), " ", text))

    def note(self, text: str) -> None:
        """Print one line outside any row, such as a retry or a wait."""
        self._end_partial()
        self._print(Text(text, style="" if self._plain else "yellow"))

    @contextmanager
    def phase(self, description: str, total: int | None = None) -> Generator[ByteAdvance]:
        """One step of the row it belongs to; a step of a new row ends the row before it.

        Yields:
            A function to call with the bytes done since the last call.
        """
        row = self._rows(description)
        if row is None:
            yield _ignore
            return
        if self._row is None or row.label != self._row.label:
            self._close_row()
            self._open_row(row)
        elif row.detail:
            self._row = row
        if row.download and total is not None:
            self._sized(total)

        def advance(amount: int) -> None:
            self._done += amount
            if self._progress is not None and self._task is not None:
                self._progress.advance(self._task, amount)

        yield advance

    def close(self, *, complete: bool) -> None:
        """End the output: print the last row when the work is `complete`, else just stop the live display."""
        if complete:
            self._close_row()
            return
        self._stop_live()
        self._end_partial()
        self._row = None

    def _open_row(self, row: Row) -> None:
        if self._header is not None:
            header = self._header
            self._header = None
            self._print(header if self._plain else Text.assemble(_playful.glyph(), " ", header))
        self._row = row
        self._started = self._console.get_time()
        self._done, self._total = 0, None
        if self._plain:
            if row.download:
                self._print_on(f"  {row.label}")
                self._partial = True
            return
        if not self._live:
            return
        columns: list[ProgressColumn] = (
            [TextColumn(f"  {row.label} "), BarColumn(bar_width=BAR_WIDTH), DownloadColumn(), TimeElapsedColumn()]
            if row.download
            else [TextColumn("  "), SpinnerColumn(), TextColumn(row.label), TimeElapsedColumn()]
        )
        self._progress = RichProgress(*columns, console=self._console, transient=True)
        self._progress.start()
        self._task = self._progress.add_task(row.label, total=None)

    def _sized(self, total: int) -> None:
        self._total, self._done = total, 0
        if self._progress is not None and self._task is not None:
            self._progress.update(self._task, total=total, completed=0)
        if self._partial:
            self._print_on(f" {total / 1e6:.1f} MB ...")

    def _stop_live(self) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = self._task = None

    def _close_row(self) -> None:
        row = self._row
        if row is None:
            return
        self._stop_live()
        self._row = None
        elapsed = max(0.0, self._console.get_time() - self._started)
        if not row.download:
            self._print(Text.assemble("  ", self.mark(), " ", f"{row.label:<10} {row.detail}".rstrip()))
            return
        size = "" if self._total is None else f"{self._total / 1e6:.1f} MB"
        if self._plain:
            ending = f" done ({elapsed:.0f} s)"
            words = " ".join(part for part in (row.label, size, "...") if part)
            self._print(ending if self._partial else f"  {words}{ending}")
            self._partial = False
            return
        filled = BAR_WIDTH if not self._total else min(BAR_WIDTH, round(BAR_WIDTH * self._done / self._total))
        bar = Text.assemble(("━" * filled, "green"), ("━" * (BAR_WIDTH - filled), "bright_black"))
        amount = "" if self._total is None else f"{self._done / 1e6:.1f}/{size}"
        clock = str(timedelta(seconds=int(elapsed)))
        self._print(Text.assemble(f"  {row.label}  ", bar, "  ", f"{amount}  {clock}".lstrip()))


BAR_WIDTH = 40
"""Cells of the download bar."""


def confirm(console: Console, question: str, *, yes: bool, flag: str = "--yes", default: bool = True) -> bool:
    """Ask a yes/no question only where a person can answer; everywhere else the flag decides.

    Args:
        console: The error console the question is shown on.
        question: The question, as plain text.
        yes: Whether the person already passed `flag`.
        flag: The option that answers for scripts and redirected runs.
        default: The answer when only Enter is pressed.

    Returns:
        Whether to go ahead.

    Raises:
        HeinzelError: When nobody can answer (stdin or `console` is not a terminal) and `yes` is not set.
    """
    if yes:
        return True
    if not (console.is_terminal and sys.stdin.isatty()):
        msg = f"{question} Not asking because this is not an interactive terminal: pass {flag} to agree."
        raise HeinzelError(msg)
    return Confirm.ask(question, console=console, default=default)
