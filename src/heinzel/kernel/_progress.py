"""Progress reporting that works in every head.

Services ask for `Progress` by type and wrap long-running work in `progress.task(...)`. The kernel's default reports
nothing; a head binds its own implementation (the CLI shows spinners and bars on stderr).

Work that produces output as it goes, such as an agent writing an answer, streams it through `progress.emitter()`:
the CLI prints it, the web head sends it to the browser as server-sent events (ADR 0045), every other head drops it.
"""

from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager

Advance = Callable[[float], None]
Emit = Callable[[str, Mapping[str, object]], None]
"""Streams one event of the current work: a kind (`text`, `tool`) and its JSON-serialisable data."""


class Progress:
    """Reports progress of long-running work. This base implementation is silent."""

    @contextmanager
    def task(self, description: str, total: float | None = None) -> Generator[Advance]:
        """Report work on `description`.

        Yields:
            A function to call with the amount of work done since the last call (of `total`, if known).
        """
        del description, total
        yield lambda _amount: None

    def emitter(self) -> Emit:
        """A function that streams events of the work the caller is doing now to whoever asked for it.

        Call it on the thread that runs the operation (a head may route events by the request that runs there); the
        function it returns may then be called from any thread. The base implementation drops every event.
        """
        return _drop


def _drop(kind: str, data: Mapping[str, object]) -> None:
    del kind, data
