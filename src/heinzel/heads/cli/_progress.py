"""The CLI head's progress: Rich spinners and bars on stderr, silent when the output is JSON."""

import sys
import time
from collections.abc import Generator, Mapping
from contextlib import contextmanager

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.progress import Progress as RichProgress
from rich.prompt import Confirm
from rich.text import Text

from heinzel import HeinzelError
from heinzel.heads._desktop_report import Advance as ByteAdvance
from heinzel.heads.cli import _playful
from heinzel.heads.cli._render import TerminalSafe
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


PLAIN_INTERVAL = 2.0
"""Seconds between the byte-count lines of a download that nobody watches live."""


class PhaseReporter:
    """Reports the phases of a long operation on the error console, so stdout keeps only the result."""

    def __init__(self, console: Console) -> None:
        """Report on `console` (stderr)."""
        self._console = console
        self._live = console.is_terminal

    def _print(self, text: str, style: str | None = None) -> None:
        self._console.print(TerminalSafe(Text(text, style=style or "")), highlight=False, soft_wrap=True)

    def note(self, text: str) -> None:
        """Print one line outside any phase."""
        self._print(text, "yellow" if self._live else None)

    @contextmanager
    def phase(self, description: str, total: int | None = None) -> Generator[ByteAdvance]:
        """Show one step: a spinner or download bar on a terminal, otherwise a start and an end line.

        Yields:
            A function to call with the bytes done since the last call.
        """
        started = time.monotonic()
        if self._live:
            columns: list[ProgressColumn] = [SpinnerColumn(), TextColumn("{task.description}")]
            if total is None:
                columns.append(TimeElapsedColumn())
            else:
                columns += [BarColumn(), DownloadColumn(), TransferSpeedColumn(), TimeRemainingColumn()]
            with RichProgress(*columns, console=self._console, transient=True) as progress:
                task = progress.add_task(description, total=total)
                yield lambda amount: progress.advance(task, amount)
            self._print(f"✓ {description} ({time.monotonic() - started:.1f}s)", "green")
            return
        self._print(f"{description}...")
        done = 0
        last = started

        def advance(amount: int) -> None:
            nonlocal done, last
            done += amount
            now = time.monotonic()
            if total is not None and now - last >= PLAIN_INTERVAL:
                last = now
                self._print(f"  {done / 1e6:.1f} of {total / 1e6:.1f} MB ({100 * done // total}%)")

        yield advance
        self._print(f"{description}: done ({time.monotonic() - started:.1f}s)")


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
