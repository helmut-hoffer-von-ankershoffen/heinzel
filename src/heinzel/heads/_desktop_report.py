"""What the desktop installer tells the person running it: phases, byte counts and notes."""

from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Protocol

type Advance = Callable[[int], None]


class Reporter(Protocol):
    """Receives the installer's progress; heads decide how it looks."""

    def phase(self, description: str, total: int | None = None) -> AbstractContextManager[Advance]:
        """Report one step; the context yields a function to call with the bytes done since the last call."""
        ...

    def note(self, text: str) -> None:
        """Report one line outside any step, such as a retry or a wait."""
        ...


class _Silent:
    @contextmanager
    def phase(self, description: str, total: int | None = None) -> Generator[Advance]:
        del description, total
        yield lambda _amount: None

    def note(self, text: str) -> None:
        del text


SILENT: Reporter = _Silent()
"""The reporter of background updates, which must not print."""


@dataclass(frozen=True)
class Row:
    """One line of output that one or more installer steps fold into; heads decide how it looks."""

    label: str
    detail: str = ""
    download: bool = False
