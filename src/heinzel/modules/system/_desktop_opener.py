"""Own desktop opener children until exit without blocking their callers."""

import errno
import subprocess
from collections.abc import Sequence
from threading import Condition, Thread

_MAX_OPENERS = 16
_POLL_SECONDS = 0.05


class _OpenerReaper:
    def __init__(self, capacity: int = _MAX_OPENERS) -> None:
        self._capacity = capacity
        self._changed = Condition()
        self._children: dict[object, subprocess.Popen[bytes] | None] = {}
        self._running = False

    def launch(self, command: Sequence[str], *, detached: bool) -> None:
        slot = object()
        with self._changed:
            if len(self._children) >= self._capacity:
                msg = "Too many desktop openers are running. Try again after one exits."
                raise OSError(errno.EAGAIN, msg)
            if not self._running:
                try:
                    Thread(target=self._reap, name="hz-open-reaper", daemon=True).start()
                except RuntimeError as exc:
                    msg = "Unable to start desktop opener cleanup"
                    raise OSError(errno.EAGAIN, msg) from exc
                self._running = True
            # Reserve an entry even while Popen is in flight. The worker cannot retire while it exists.
            self._children[slot] = None

        child = None
        try:
            child = subprocess.Popen(  # noqa: S603 - callers resolve a fixed desktop opener, arguments only
                list(command),
                stdin=subprocess.DEVNULL if detached else None,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=detached,
            )
        finally:
            with self._changed:
                if child is None:
                    del self._children[slot]
                else:
                    self._children[slot] = child
                self._changed.notify()

    def _reap(self) -> None:
        with self._changed:
            while self._children:
                has_child = False
                for slot, child in tuple(self._children.items()):
                    if child is None:
                        continue
                    if child.poll() is None:
                        has_child = True
                    else:
                        del self._children[slot]
                if self._children:
                    self._changed.wait(_POLL_SECONDS if has_child else None)
            self._running = False


# Process-wide so the configuration and notification openers share the same limit.
_OPENERS = _OpenerReaper()


def launch_desktop_opener(command: Sequence[str], *, detached: bool = False) -> None:
    """Start a resolved desktop opener after its background reap has been reserved."""
    _OPENERS.launch(command, detached=detached)
