"""Progress reporting that works in every head.

Services ask for `Progress` by type and wrap long-running work in `progress.task(...)`. The kernel's default reports
nothing; a head binds its own implementation (the CLI shows spinners and bars on stderr).
"""

from collections.abc import Callable, Generator
from contextlib import contextmanager

Advance = Callable[[float], None]


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
