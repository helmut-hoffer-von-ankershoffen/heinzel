"""The named steps of a write the app shows as it runs: installing the daemon, updating hz and its plugins.

A step is real work: the web app lists them as they start and end (a checklist, never an invented percentage), and the
command line shows a spinner for the one that is running. A head that doesn't show progress drops them.

Every step is also logged, whatever the head: `operation.step.start` when it runs and `operation.step.outcome` when it
ends, with fields, never formatted text: `operation`, `step`, `plugin` (for a plugin's step), `outcome`, `duration_ms`
and `detail` (the words the person reads, which never hold a secret).
"""

import logging
import time
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from typing import Literal

from heinzel import HeinzelError, Progress
from heinzel.kernel._logging import get_logger, log_event

State = Literal["pending", "running", "done", "warn", "skipped", "failed"]
EVENT = "phase"
"""The kind of the event a streaming head sends: `{id, label, state, detail?}` for each step, in order."""
START = "operation.step.start"
OUTCOME = "operation.step.outcome"
logger = get_logger("operation")


def _event(name: str, fields: dict[str, object], level: int = logging.INFO) -> None:
    """One structured operation event: the stable name and fields, never a formatted sentence."""
    log_event(logger, name, level=level, **fields)  # type: ignore[arg-type]  # step fields never include level or exc_info


class Phases:
    """Tells a head's `Progress` which step runs, and how it ended."""

    def __init__(
        self,
        progress: Progress,
        plan: Sequence[tuple[str, str]] = (),
        *,
        operation: str = "system.daemon",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Report to `progress` and announce the `plan` of (id, label) steps, so a head can say "step 2 of 5".

        `operation` names the operation in the log events, e.g. `system.update`. Call from the thread that runs the
        operation, as the head routes events by it.
        """
        self._progress = progress
        self._emit = progress.emitter()
        self._operation = operation
        self._clock = clock
        self._started: dict[str, float] = {}
        for id_, label in plan:
            self.note(id_, label, "pending")

    def note(
        self, id_: str, label: str, state: State, detail: str | None = None, *, version: str | None = None
    ) -> None:
        """Say how a step stands, with what the person should know about it (and the `version` it is about)."""
        data: dict[str, object] = {"id": id_, "label": label, "state": state}
        if detail:
            data["detail"] = detail
        self._emit(EVENT, data)
        self._log(id_, state, detail, version)

    def _log(self, id_: str, state: State, detail: str | None, version: str | None) -> None:
        """One start event when a step runs, one outcome event when it ends; a planned step logs nothing yet.

        A step told only by its outcome (a plugin the install reported on) still logs its start first.
        """
        if state == "pending":
            return
        fields: dict[str, object] = {"operation": self._operation, "step": id_}
        kind, _, name = id_.partition(":")
        if name and kind in {"plugin", "needs"}:
            fields["plugin"] = name
        if version:
            fields["version"] = version
        if state == "running":
            self._started[id_] = self._clock()
            _event(START, fields | {"phase": "start"})
            return
        started = self._started.pop(id_, None)
        if started is None:
            started = self._clock()
            _event(START, fields | {"phase": "start"})
        fields |= {"outcome": state, "duration_ms": round((self._clock() - started) * 1000), "detail": detail}
        level = logging.WARNING if state in {"failed", "warn"} else logging.INFO
        _event(OUTCOME, fields | {"phase": "end"}, level)

    @contextmanager
    def step(self, id_: str, label: str) -> Generator[None]:
        """Run a step: it is running inside, done after, and failed with the error's words when it raises.

        Raises:
            HeinzelError: Whatever the step raised, after the failed step was told.
        """
        self.note(id_, label, "running")
        state: State = "failed"
        detail: str | None = None
        try:
            with self._progress.task(label):
                yield
            state = "done"
        except HeinzelError as exc:
            detail = str(exc)
            raise
        finally:
            self.note(id_, label, state, detail)
