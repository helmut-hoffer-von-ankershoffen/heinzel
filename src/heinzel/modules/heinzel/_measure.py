"""Measuring a contract's drafts: how long drafting took, and how much of a draft changed before it was sent.

This is the success test of a drafting contract: does it save time, and how much does the person who sends the draft
still change? Both are about the draft, never about people: drafting time comes from the render, and the change is a
word-level diff ratio between the draft and the text the person says they sent (`hz heinzel sent`). Everything stays
in the draft's directory, next to its record: `<stem>.sent.json` beside `<stem>.md` and `<stem>.record.json`. The sent
text itself is never copied; only its size, hash and the ratio are kept.
"""

import hashlib
import json
import re
import statistics
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

SENT_SUFFIX = ".sent.json"
"""What `hz heinzel sent` recorded about a draft, next to it."""
MAX_SENT_BYTES = 256 * 1024
"""A sent text larger than this is refused: a report is a few kilobytes, and the diff is quadratic in the worst case."""
MEASURED = 52
"""How many of the newest drafts `hz heinzel show` measures: a year of weekly ones."""
RECENT = 8
"""How many of the newest drafts `hz heinzel show` lists one by one."""
_WORD = re.compile(r"\S+")


class Sent(BaseModel):
    """`hz heinzel sent`: how much a draft changed before it was sent."""

    contract: str = Field(description="The qualified contract, `<namespace>:<name>`.")
    draft: Path = Field(description="The draft compared.")
    sent: str = Field(description="Where the approved text was read: a file's path, or `documents:<ref>`.")
    sent_sha256: str = Field(description="SHA-256 of the sent text; the text itself isn't kept.")
    recorded_at: datetime = Field(description="When this was recorded.")
    changed: float = Field(
        ge=0, le=1, description="Share of the draft's words changed: 0 sent as drafted, 1 rewritten."
    )
    draft_words: int = Field(description="Words in the draft.")
    sent_words: int = Field(description="Words in what was sent.")
    edit_minutes: float | None = Field(
        default=None, description="Minutes spent editing, if you gave your own estimate; none otherwise."
    )
    record: Path = Field(description="Where this was saved, next to the draft.")


class RunMeasure(BaseModel):
    """One draft, measured: how long drafting took, what it cost and read, and how much changed before sending."""

    draft: Path = Field(description="The draft.")
    created: datetime = Field(description="When its render fetched the inputs.")
    drafting_seconds: float | None = Field(
        description="Wall-clock seconds from fetching the inputs to saving the draft; none if the record lacks it."
    )
    cost_usd: float | None = Field(description="What the agent session cost, as far as the runner reported.")
    inputs: dict[str, str] = Field(description="The lineage: SHA-256 of each input's data, by name.")
    changed: float | None = Field(description="Share of its words changed before sending; none until recorded.")
    edit_minutes: float | None = Field(description="Your own estimate of the minutes spent editing, if given.")


class Measurements(BaseModel):
    """A contract's success test so far: its drafts, how long drafting took and how much was changed."""

    drafts: int = Field(description="Drafts saved so far.")
    sent: int = Field(description="Drafts with a recorded sent version.")
    median_drafting_seconds: float | None = Field(description="Median drafting time; none without timed drafts.")
    mean_changed: float | None = Field(description="Mean share of words changed before sending; none until recorded.")
    total_edit_minutes: float | None = Field(description="Sum of your editing estimates; none if you gave none.")
    recent: list[RunMeasure] = Field(description=f"The newest drafts, at most {RECENT}, newest first.")


def words(text: str) -> list[str]:
    """The words of `text`: runs of non-whitespace, so reflowing a paragraph changes nothing."""
    return _WORD.findall(text)


def changed_ratio(draft: str, sent: str) -> float:
    """The share of words that differ between `draft` and `sent`: 0 when equal up to whitespace, 1 when disjoint."""
    before, after = words(draft), words(sent)
    if not before and not after:
        return 0.0
    return round(1 - SequenceMatcher(None, before, after, autojunk=False).ratio(), 4)


def compare(draft: str, sent: str) -> tuple[float, int, int, str]:
    """The changed ratio, both word counts and the sent text's SHA-256."""
    digest = "sha256:" + hashlib.sha256(sent.encode()).hexdigest()
    return changed_ratio(draft, sent), len(words(draft)), len(words(sent)), digest


def sent_path(draft: Path) -> Path:
    """Where `hz heinzel sent` records a draft's sent version."""
    return draft.with_name(draft.stem + SENT_SUFFIX)


def is_measure_file(path: Path) -> bool:
    """Whether `path` is a sent record rather than a draft."""
    return path.name.endswith(SENT_SUFFIX)


def _json(path: Path | None) -> dict[str, Any]:
    """A JSON object from `path`, or empty when there is none or it can't be read: measuring never fails a listing."""
    if path is None or not path.is_file() or path.is_symlink():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return cast("dict[str, Any]", data) if isinstance(data, dict) else {}


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def measure(draft: Path, created: datetime, record: Path | None) -> RunMeasure:
    """One draft's measures, from its record and its sent record; missing parts are none."""
    rendered = cast("dict[str, Any]", _json(record).get("rendered") or {})
    agent = cast("dict[str, Any]", rendered.get("agent") or {})
    usage = cast("dict[str, Any]", agent.get("usage") or {})
    sent = _json(sent_path(draft))
    inputs = rendered.get("inputs")
    seconds = _number(rendered.get("seconds"))
    return RunMeasure(
        draft=draft,
        created=created,
        drafting_seconds=seconds if seconds is not None else _number(agent.get("duration_seconds")),
        cost_usd=_number(usage.get("cost_usd")),
        inputs={str(k): str(v) for k, v in cast("dict[str, object]", inputs).items()}
        if isinstance(inputs, dict)
        else {},
        changed=_number(sent.get("changed")),
        edit_minutes=_number(sent.get("edit_minutes")),
    )


def summary(runs: list[RunMeasure]) -> Measurements:
    """The success test over `runs`, newest first."""
    timed = [r.drafting_seconds for r in runs if r.drafting_seconds is not None]
    changed = [r.changed for r in runs if r.changed is not None]
    minutes = [r.edit_minutes for r in runs if r.edit_minutes is not None]
    return Measurements(
        drafts=len(runs),
        sent=len(changed),
        median_drafting_seconds=statistics.median(timed) if timed else None,
        mean_changed=round(statistics.fmean(changed), 4) if changed else None,
        total_edit_minutes=sum(minutes) if minutes else None,
        recent=runs[:RECENT],
    )
