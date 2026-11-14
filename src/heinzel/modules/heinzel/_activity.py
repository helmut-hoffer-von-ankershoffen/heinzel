"""The one-word state of a Heinzel, computed from its journal by fixed precedence, never by the model.

Everything here is a pure read of control-journal envelopes the caller already holds: no private bodies are opened,
so a listing stays one journal read per Heinzel. Absent or malformed data counts as nothing happening, never as an
error, so an unknown Heinzel shows as idle.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from heinzel import SessionEvent
from heinzel.modules.heinzel._turn_activity import interactive_turn_id

Activity = Literal[
    "paused", "stuck", "waiting_for_you", "overloaded", "working", "waiting_on_others", "proud", "asleep", "idle"
]

# First match wins; idle is the fallback when nothing else applies.
PRECEDENCE: tuple[Activity, ...] = (
    "paused",
    "stuck",
    "waiting_for_you",
    "overloaded",
    "working",
    "waiting_on_others",
    "proud",
    "asleep",
)

# More than one run waiting behind the current one is a backlog the Heinzel is not keeping up with.
OVERLOAD_QUEUE = 2
RECENT = timedelta(hours=1)

_FINISHED = frozenset({"message.replied", "interactive.abandoned"})
_FAILED = frozenset({"interactive.abandoned", "agent.schedule-failed"})
_OWNER_APPROVED = frozenset({"approval.issued", "draft.published"})


@dataclass(frozen=True)
class ActivityFacts:
    """What code knows about one Heinzel; each flag is false when its data is absent."""

    paused: bool = False
    stuck: bool = False
    waiting_for_you: bool = False
    overloaded: bool = False
    working: bool = False
    waiting_on_others: bool = False
    proud: bool = False
    asleep: bool = False


def activity(facts: ActivityFacts) -> Activity:
    """Return the first state of the fixed precedence that applies, or idle."""
    for state in PRECEDENCE:
        if getattr(facts, state):
            return state
    return "idle"


@dataclass(frozen=True)
class RunCounts:
    """Counts of runs (turns) projected from journal envelopes alone."""

    runs_last_hour: int = 0
    queued_runs: int = 0
    running: bool = False
    last_run_failed: bool = False
    last_approved_at: datetime | None = None


def open_write_drafts(events: Iterable[SessionEvent]) -> list[SessionEvent]:
    """Captured-write drafts still waiting for their owner, oldest first: neither discarded nor attempted."""
    ordered = list(events)
    settled = {e.key for e in ordered if e.kind == "write.discarded"} | {
        e.key.removeprefix("write-attempt-") for e in ordered if e.kind == "write.attempted" and e.key
    }
    return [e for e in ordered if e.kind == "write.drafted" and e.key not in settled]


def run_counts(events: Iterable[SessionEvent], now: datetime) -> RunCounts:
    """Project runs from envelopes: started, finished, queued, failed and the owner's latest approval."""
    ordered = list(events)
    started = {e.key for e in ordered if e.kind == "interactive.started" and e.key}
    finished = {e.key for e in ordered if e.kind in _FINISHED and e.key}
    queued = 0
    for event in ordered:
        message = event.data.get("message_id") if event.kind == "message.accepted" else None
        if isinstance(message, str) and message:
            turn = interactive_turn_id(message)
            queued += turn not in started and turn not in finished
    outcome = next((e.kind for e in reversed(ordered) if e.kind in _FINISHED | _FAILED), None)
    approvals = [e.ts for e in ordered if e.kind in _OWNER_APPROVED]
    return RunCounts(
        runs_last_hour=sum(e.kind == "interactive.started" and recently(e.ts, now) for e in ordered),
        queued_runs=queued,
        running=bool(started - finished),
        last_run_failed=outcome in _FAILED,
        last_approved_at=max(approvals, default=None),
    )


def recently(moment: datetime | None, now: datetime) -> bool:
    """Whether a moment lies within the last hour, never in the future."""
    return moment is not None and timedelta(0) <= now - moment <= RECENT
