"""The named steps of installing and removing the daemon, told to whoever shows progress.

A step is real work: the web app lists them as they start and end (a checklist, never an invented percentage), and the
command line shows a spinner for the one that is running. A head that doesn't show progress drops them.
"""

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from typing import Literal

from heinzel import HeinzelError, Progress

State = Literal["pending", "running", "done", "warn", "skipped", "failed"]
EVENT = "phase"
"""The kind of the event a streaming head sends: `{id, label, state, detail?}` for each step, in order."""


class Phases:
    """Tells a head's `Progress` which step runs, and how it ended."""

    def __init__(self, progress: Progress, plan: Sequence[tuple[str, str]] = ()) -> None:
        """Report to `progress` and announce the `plan` of (id, label) steps, so a head can say "step 2 of 5".

        Call from the thread that runs the operation, as the head routes events by it.
        """
        self._progress = progress
        self._emit = progress.emitter()
        for id_, label in plan:
            self.note(id_, label, "pending")

    def note(self, id_: str, label: str, state: State, detail: str | None = None) -> None:
        """Say how a step stands, with what the person should know about it."""
        data: dict[str, object] = {"id": id_, "label": label, "state": state}
        if detail:
            data["detail"] = detail
        self._emit(EVENT, data)

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
