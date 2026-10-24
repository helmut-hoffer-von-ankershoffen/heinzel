"""The ai module: how hz reaches AI (ADR 0057).

Its submodule `access` lists the AI access options installed on this machine (Claude Code, a company gateway, ones
plugins add), says which one is in use and whether each is signed in, tests one on request and switches between them.
`hz ai models` lists the models and efforts of the one in use, for the chat's picker.
"""

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from heinzel import Adapters, AiAccess, Module
from heinzel.kernel import CapabilityView
from heinzel.modules.ai._models import EffortChoice, ModelChoice, ModelList
from heinzel.modules.ai._models import models as listed

module = Module(
    "ai", "How hz reaches AI: the AI access in use, its status and a live test.", requires=["ai-access:read"]
)


def render(result: ModelList) -> RenderableType:
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
@module.operation(render=render, heads={"cli", "mcp", "web"})
def models(options: Adapters[AiAccess], view: CapabilityView) -> ModelList:
    """List the models and efforts of the AI access in use, for the chat's model and effort picker."""
    return listed(options, view)


__all__ = ["EffortChoice", "ModelChoice", "ModelList", "module"]
