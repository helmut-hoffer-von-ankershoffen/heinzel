"""What runs spent: a local ledger of tokens and cost per run, and the reports `hz ai usage` and the footer read.

One JSON line per run in `<state_dir>/ai/usage.jsonl`, readable by this user only: counts, ids and the cause, never
prompt or answer text. Lines older than KEEP are dropped when the file grows past LIMIT bytes.
"""

import contextlib
import logging
import os
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, ValidationError

from heinzel import Config, ModuleSettings, Settings, UsageEntry, UsageLog
from heinzel.modules.ai.access._models import Bindings

logger = logging.getLogger(__name__)

FILE = "usage.jsonl"
KEEP = timedelta(days=60)
LIMIT = 4 * 1024 * 1024
Period = Literal["today", "week"]
Dimension = Literal["access", "model", "heinzel", "cause"]
DIMENSIONS: tuple[Dimension, ...] = ("access", "model", "heinzel", "cause")


class AiSettings(ModuleSettings):
    """Settings of the ai module: the `[ai]` table or `HZ_AI__*` variables. A project `hz.toml` can't set them."""

    daily_budget_usd: float = Field(
        default=5.0,
        gt=0,
        allow_inf_nan=False,
        title="Daily AI budget",
        description="What you mean to spend on AI a day, in US dollars: the footer's meter fills against it and turns "
        "amber at 75% and red at 100%. Nothing is stopped by it; each run keeps its own cap.",
        json_schema_extra={"x-unit": "usd"},
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"daily_budget_usd"})


class UsageRow(BaseModel):
    """What one access option, model, Heinzelmännchen or cause spent in the period."""

    key: str = Field(description="The access option, model, Heinzelmännchen or cause; `unknown` when not reported.")
    runs: int = Field(description="Runs counted.")
    tokens: int = Field(description="Every token moved, cache reads and writes included.")
    input_tokens: int = Field(description="Tokens sent, without cache reads.")
    output_tokens: int = Field(description="Tokens produced.")
    cost_usd: float = Field(description="What the runs that report a cost cost, in US dollars.")
    unpriced: int = Field(description="Runs that reported no cost, so the cost is at least `cost_usd`.")


class UsageTotal(BaseModel):
    """What a period spent in all."""

    since: datetime = Field(description="The start of the period, in UTC: local midnight today, or seven days ago.")
    runs: int = Field(description="Runs counted.")
    tokens: int = Field(description="Every token moved.")
    cost_usd: float = Field(description="What the priced runs cost, in US dollars.")
    unpriced: int = Field(description="Runs that reported no cost.")


class UsageReport(BaseModel):
    """Today's and this week's tokens and cost, and one period broken down by access, model, Heinzelmännchen, cause."""

    period: Period = Field(description="The period the breakdowns cover: `today` or `week` (the last seven days).")
    by: Dimension = Field(description="The breakdown the CLI shows; all four are in `breakdowns`.")
    today: UsageTotal = Field(description="Since local midnight.")
    week: UsageTotal = Field(description="The last seven days.")
    budget_usd: float = Field(description="`[ai] daily_budget_usd`, what today's meter fills against.")
    level: float = Field(description="Today's cost against the daily budget, 0 to 1 (capped), for a meter.")
    tone: Literal["ok", "warn", "over"] = Field(description="`ok` below 75% of the budget, `warn` to 100%, `over`.")
    label: str = Field(description="A short label for a status item, e.g. `182k tok`.")
    breakdowns: dict[Dimension, list[UsageRow]] = Field(
        description="The period's rows by access, model, Heinzelmännchen and cause, most tokens first."
    )


def compact(tokens: int) -> str:
    """`182k tok`, `1.2M tok`, `950 tok`."""
    if tokens >= 1_000_000:  # noqa: PLR2004 - a million
        return f"{tokens / 1_000_000:.1f}M tok"
    if tokens >= 1_000:  # noqa: PLR2004 - a thousand
        return f"{tokens / 1_000:.0f}k tok" if tokens >= 10_000 else f"{tokens / 1_000:.1f}k tok"  # noqa: PLR2004
    return f"{tokens} tok"


class UsageLedger(UsageLog):
    """The usage log hz keeps on this machine (ADR 0057)."""

    def __init__(self, settings: Settings, config: Config) -> None:
        """Keep the ledger in the state directory; `config` is the kernel's, which names the AI access in use."""
        self.path = settings.state_dir / "ai" / FILE
        self._config = config

    def _access(self) -> str | None:
        """The AI access in use, as the kernel's configuration says (the daemon's or web head's, not the cwd's)."""
        try:
            return self._config.settings(Bindings, "capabilities").ai_access
        except Exception:  # noqa: BLE001 - a broken value never fails a run's recording
            return None

    def record(self, entry: UsageEntry) -> None:
        """Append `entry`, with the AI access in use when it doesn't name one; never raises."""
        try:
            if entry.access is None:
                entry = entry.model_copy(update={"access": self._access()})
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            line = entry.model_dump_json() + "\n"
            descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as file:
                file.write(line)  # one short line per write: appends don't interleave
            if self.path.stat().st_size > LIMIT:
                self._prune()
        except Exception as exc:  # noqa: BLE001 - recording never fails the run it records
            logger.warning("Recording AI usage failed: %s", type(exc).__name__)

    def _prune(self) -> None:
        cutoff = datetime.now(UTC) - KEEP
        # The newest lines within KEEP, and never more than half the limit, so a busy ledger isn't rewritten each run.
        kept = [e.model_dump_json() for e in self.entries() if e.at >= cutoff]
        while kept and sum(len(line) + 1 for line in kept) > LIMIT // 2:
            kept = kept[len(kept) // 4 or 1 :]
        handle, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".usage-", suffix=".jsonl")  # 0600
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                file.write("".join(f"{line}\n" for line in kept))
            Path(temporary).replace(self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def entries(self, since: datetime | None = None) -> list[UsageEntry]:
        """The entries since `since` (all when none); unreadable lines are skipped."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        found: list[UsageEntry] = []
        for line in lines:
            with contextlib.suppress(ValidationError, ValueError):
                entry = UsageEntry.model_validate_json(line)
                if since is None or entry.at >= since:
                    found.append(entry)
        return found


def _key(entry: UsageEntry, dimension: Dimension) -> str:
    return getattr(entry, dimension) or "unknown"


def total(entries: Iterable[UsageEntry], since: datetime) -> UsageTotal:
    """The sum of `entries` from `since`."""
    chosen = [e for e in entries if e.at >= since]
    return UsageTotal(
        since=since,
        runs=len(chosen),
        tokens=sum(e.tokens for e in chosen),
        cost_usd=round(sum(e.cost_usd or 0.0 for e in chosen), 6),
        unpriced=sum(1 for e in chosen if e.cost_usd is None),
    )


def breakdown(entries: Iterable[UsageEntry], dimension: Dimension) -> list[UsageRow]:
    """`entries` summed by `dimension`, most tokens first."""
    groups: dict[str, list[UsageEntry]] = defaultdict(list)
    for entry in entries:
        groups[_key(entry, dimension)].append(entry)
    rows = [
        UsageRow(
            key=key,
            runs=len(group),
            tokens=sum(e.tokens for e in group),
            input_tokens=sum(e.input_tokens for e in group),
            output_tokens=sum(e.output_tokens for e in group),
            cost_usd=round(sum(e.cost_usd or 0.0 for e in group), 6),
            unpriced=sum(1 for e in group if e.cost_usd is None),
        )
        for key, group in groups.items()
    ]
    return sorted(rows, key=lambda row: (-row.tokens, row.key))


def midnight(now: datetime) -> datetime:
    """Local midnight of `now`'s day, in UTC."""
    local = now.astimezone()
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def report(
    ledger: UsageLedger, settings: AiSettings, period: Period, by: Dimension, now: datetime | None = None
) -> UsageReport:
    """Today's and this week's totals, the period's breakdowns, and today's level against the daily budget."""
    now = now or datetime.now(UTC)
    week_start = now - timedelta(days=7)
    entries = ledger.entries(since=min(week_start, midnight(now)))
    today, week = total(entries, midnight(now)), total(entries, week_start)
    chosen = [e for e in entries if e.at >= (today.since if period == "today" else week.since)]
    ratio = today.cost_usd / settings.daily_budget_usd
    tone: Literal["ok", "warn", "over"] = "over" if ratio >= 1 else "warn" if ratio >= 0.75 else "ok"  # noqa: PLR2004
    return UsageReport(
        period=period,
        by=by,
        today=today,
        week=week,
        budget_usd=settings.daily_budget_usd,
        level=min(ratio, 1.0),
        tone=tone,
        label=compact(today.tokens),
        breakdowns={dimension: breakdown(chosen, dimension) for dimension in DIMENSIONS},
    )


__all__ = ["AiSettings", "UsageLedger", "UsageReport", "UsageRow", "UsageTotal", "compact", "report"]
