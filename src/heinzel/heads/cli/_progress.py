"""The CLI head's progress: Rich spinners and bars on stderr, silent when the output is JSON."""

from collections.abc import Generator, Mapping
from contextlib import contextmanager

from rich.console import Console
from rich.progress import BarColumn, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.progress import Progress as RichProgress

from heinzel.kernel import Emit, Progress
from heinzel.kernel._progress import Advance


class ConsoleProgress(Progress):
    """Shows progress on the error console, so stdout stays clean for results."""

    def __init__(self, console: Console) -> None:
        """Report on `console` (stderr)."""
        self._console = console
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
        columns = [SpinnerColumn(), TextColumn("{task.description}")]
        if total is not None:
            columns += [BarColumn(), TaskProgressColumn()]
        with RichProgress(*columns, TimeElapsedColumn(), console=self._console, transient=True) as progress:
            task_id = progress.add_task(description, total=total)
            yield lambda amount: progress.advance(task_id, amount)
