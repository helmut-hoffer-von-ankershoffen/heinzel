"""The ai module: how hz reaches AI and what it spends (ADR 0057).

Its submodule `access` lists the AI access options installed on this machine (Claude Code, a company gateway, a model
API through Pydantic AI, ones plugins add), says which one is in use and whether each is signed in, tests one on
request and switches between them. `hz ai models` lists the models and efforts of the one in use, for the chat's
picker. The module itself keeps what every run spent (`UsageLog`) and reports it with `hz ai usage`.
"""

from typing import Annotated

from pydantic import Field
from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from heinzel import Adapters, AiAccess, Module, UsageLog
from heinzel.kernel import CapabilityView
from heinzel.modules.ai._models import EffortChoice, ModelChoice, ModelList
from heinzel.modules.ai._models import models as listed
from heinzel.modules.ai._usage import (
    AiSettings,
    Dimension,
    Period,
    UsageLedger,
    UsageReport,
    UsageRow,
    UsageTotal,
    report,
)

module = Module("ai", "How hz reaches AI, and what it spends: tokens and cost per run.", requires=["ai-access:read"])
module.settings(AiSettings)
module.service(UsageLedger)
module.service(UsageLog, UsageLedger)  # runs record through the protocol; nothing imports this module


def _money(total: UsageTotal | UsageRow) -> str:
    return f"${total.cost_usd:.2f}" + ("+" if total.unpriced else "")


def render(result: UsageReport) -> RenderableType:
    """Today and this week, today against the daily budget, and the period by the chosen dimension."""
    head = Text(
        f"Today {result.today.tokens:,} tokens · {_money(result.today)} of ${result.budget_usd:.2f} · "
        f"this week {result.week.tokens:,} tokens · {_money(result.week)}",
        style={"ok": "green", "warn": "yellow", "over": "red"}[result.tone],
    )
    table = Table(box=None, pad_edge=False)
    for column in (result.by.title(), "Runs", "Tokens", "In", "Out", "Cost"):
        table.add_column(column, justify="left" if column == result.by.title() else "right")
    for row in result.breakdowns[result.by]:
        table.add_row(
            row.key, str(row.runs), f"{row.tokens:,}", f"{row.input_tokens:,}", f"{row.output_tokens:,}", _money(row)
        )
    parts: list[RenderableType] = [
        head,
        Text(f"{'Today' if result.period == 'today' else 'Last 7 days'} by {result.by}:"),
    ]
    parts.append(table if result.breakdowns[result.by] else Text("  nothing recorded"))
    if result.today.unpriced or result.week.unpriced:
        parts.append(Text("+ some runs reported no cost, so the cost is at least what is shown.", style="dim"))
    return Group(*parts)


# What you spent is yours: the CLI, MCP and the web head (behind its session token), never the unauthenticated REST
# head (ADR 0039).
@module.operation(render=render, heads={"cli", "mcp", "web"})
def usage(
    ledger: UsageLedger,
    settings: AiSettings,
    period: Annotated[
        Period, Field(description="`today` (since local midnight) or `week` (the last seven days).")
    ] = "today",
    by: Annotated[Dimension, Field(description="Break down by `access`, `model`, `heinzel` or `cause`.")] = "model",
) -> UsageReport:
    """Show the tokens and cost of AI runs today and this week, by access, model, Heinzelmännchen or cause."""
    return report(ledger, settings, period, by)


def render_models(result: ModelList) -> RenderableType:
    """The AI access in use, its models and its efforts."""
    plan = " · uses your plan" if result.billing == "plan" else ""
    parts: list[RenderableType] = [Text(f"{result.title or 'No AI access'}{plan}", style="bold")]
    if result.models:
        parts.append(Text("Models: " + ", ".join(model.name for model in result.models)))
    if result.efforts:
        table = Table(box=None, pad_edge=False, show_header=False)
        for effort in result.efforts:
            extra = " (recommended)" if effort.recommended else (f" ({effort.warning})" if effort.warning else "")
            table.add_row(Text(effort.name), Text(effort.title + extra, style="dim"))
        parts += [Text("Efforts:"), table]
    if result.note:
        parts.append(Text(result.note, style="yellow"))
    return Group(*parts)


# What is installed and how it is billed is the person's own: the CLI, MCP and the web head, never the
# unauthenticated REST head (ADR 0039).
@module.operation(render=render_models, heads={"cli", "mcp", "web"})
def models(options: Adapters[AiAccess], view: CapabilityView) -> ModelList:
    """List the models and efforts of the AI access in use, for the chat's model and effort picker."""
    return listed(options, view)


__all__ = ["AiSettings", "EffortChoice", "ModelChoice", "ModelList", "UsageLedger", "UsageReport", "module"]
