"""Host execution marker for source-captured plugin initialization."""

import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_active: ContextVar[tuple[dict[str, object], Callable[[], bool]] | None] = ContextVar("captured_source", default=None)


@contextmanager
def executing_captured_source(module_globals: dict[str, object], verify: Callable[[], bool]) -> Iterator[None]:
    """Set the host-owned marker only for one captured module execution."""
    token = _active.set((module_globals, verify))
    try:
        yield
    finally:
        _active.reset(token)


def captured_source_active() -> bool:
    """Whether the calling module is currently executing under the host's captured source loader.

    This reports source-loading context only. It does not grant access, establish publisher trust, or approve a read.
    """
    marker = _active.get()
    if marker is None or sys._getframe(1).f_globals is not marker[0]:  # noqa: SLF001 - exact executing caller
        return False
    try:
        return marker[1]()
    except Exception:  # noqa: BLE001 - an unavailable source must not claim capture
        return False
