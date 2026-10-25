"""The access module of ai: how hz reaches AI, chosen like the theme (ADR 0057).

An AI access option is an adapter of the `ai-access` capability: `claude-code` (the Claude Code CLI, from
`system.runner`), `gateway` (a company CLI described in `[ai.access]`) or one a plugin adds. `[capabilities] ai_access`
is the one choice; `hz ai access use <name>` sets it, and binds the agent runner the option names so the chat follows.
"""

from typing import Annotated

from pydantic import Field
from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from heinzel import AccessStatus, Check, Module, Progress
from heinzel.modules.ai.access._chatgpt import CHATGPT_LOGIN, ChatGptAccess
from heinzel.modules.ai.access._chatgpt import HOSTS as CHATGPT_HOSTS
from heinzel.modules.ai.access._chatgpt import NAME as CHATGPT
from heinzel.modules.ai.access._gateway import NAME as GATEWAY
from heinzel.modules.ai.access._gateway import GatewayAccess, configured_hosts
from heinzel.modules.ai.access._models import (
    ADAPTER_PATTERN,
    AccessList,
    AccessOption,
    AccessSettings,
    AccessTestReport,
    AccessUse,
)
from heinzel.modules.ai.access._pydantic import API_KEY, PydanticAiAccess, model_hosts
from heinzel.modules.ai.access._pydantic import NAME as PYDANTIC_AI
from heinzel.modules.ai.access._service import AccessService

module = Module(
    "access",
    "Pick how hz reaches AI: Claude Code, your company's gateway, or an option a plugin adds.",
    requires=["ai-access:read"],
)
module.settings(AccessSettings)
module.service(AccessService)
module.provides("ai-access", modes={"read"}, hosts=configured_hosts, name=GATEWAY)(GatewayAccess)
module.add(API_KEY)
module.provides("ai-access", modes={"read"}, hosts=model_hosts, name=PYDANTIC_AI)(PydanticAiAccess)
module.add(CHATGPT_LOGIN)
module.provides("ai-access", modes={"read"}, hosts=CHATGPT_HOSTS, name=CHATGPT)(ChatGptAccess)
module.service(ChatGptAccess)  # `hz ai access login` signs in through it

STYLES = {"signed in": "green", "tested": "green", "not signed in": "yellow", "test failed": "red", "error": "red"}


def render(result: AccessList) -> RenderableType:
    """The options with the one in use marked, their status and hosts, what each use runs, and what to run next."""
    table = Table(box=None, pad_edge=False)
    for column in ("", "Access", "Title", "Status", "Hosts", "From"):
        table.add_column(column, overflow="fold")
    for option in result.options:
        table.add_row(
            "*" if option.in_use else "",
            Text(option.name, style="bold" if option.in_use else ""),
            Text(option.title),
            Text(option.status, style=STYLES.get(option.status, "")),
            Text(", ".join(option.hosts) or "-"),
            Text(option.plugin or "built in"),
        )
    uses = Table(box=None, pad_edge=False, show_header=False)
    for use in result.uses:
        uses.add_row(Text(use.use, style="bold"), Text(use.runs))
    parts: list[RenderableType] = [table, Text("What runs each use today:"), uses]
    if result.note:
        parts.append(Text(result.note, style="yellow"))
    if result.next:
        parts.append(Text("Next:\n" + "\n".join(f"  {step}" for step in result.next)))
    return Group(*parts)


def render_test(result: AccessTestReport) -> RenderableType:
    """Whether the option answered, what it said or why not, and what it cost."""
    cost = f", ${result.cost_usd:.4f}" if result.cost_usd is not None else ""
    model = f" ({result.model})" if result.model else ""
    head = Text(
        f"{result.name}: {'answered' if result.ok else 'failed'}{model} in {result.duration_seconds:.1f}s{cost}",
        style="green" if result.ok else "red",
    )
    parts: list[RenderableType] = [head]
    if result.detail:
        parts.append(Text(result.detail))
    if result.fix:
        parts.append(Text(f"Fix: {result.fix}"))
    return Group(*parts)


# Sign-in state and hosts are about this machine's person; a web page can reach a local REST server, so the CLI, MCP
# and the web head (behind its session token) get the listing, never the unauthenticated REST head (ADR 0039). Switching
# and testing are the person's own acts: an agent on MCP could otherwise move work off a company gateway or spend.
@module.operation(name="list", render=render, heads={"cli", "mcp", "web"})
def list_(service: AccessService) -> AccessList:
    """List the AI access options, the one in use, whether each is signed in, and what runs each use.

    Asks each option offline (e.g. `claude auth status`); calls no model and spends nothing.
    """
    return service.listing()


@module.operation(read_only=False, render=render, heads={"cli", "web"})
def use(
    service: AccessService,
    name: Annotated[
        str,
        Field(
            pattern=ADAPTER_PATTERN,
            description="The AI access to use, one `hz ai access list` shows, e.g. `claude-code`.",
        ),
    ],
) -> AccessList:
    """Use an AI access option: sets capabilities.ai_access, and agent_runner if the option names one."""
    return service.use(name)


@module.operation(name="test", read_only=False, render=render_test, heads={"cli", "web"})
def live_check(
    service: AccessService,
    name: Annotated[
        str | None,
        Field(
            pattern=ADAPTER_PATTERN, description="The option to test, e.g. `gateway`; the one in use when not given."
        ),
    ] = None,
) -> AccessTestReport:
    """Run one cheap live check of an AI access option and keep the result; this may cost a little.

    Claude Code answers one line within $0.05; the gateway runs its configured test command.
    """
    return service.test(name)


# Opens a browser and waits for its callback on 127.0.0.1: a person's own act, in a terminal (ADR 0057).
@module.operation(read_only=False, heads={"cli"})
def login(access: ChatGptAccess, progress: Progress) -> AccessStatus:
    """Sign in to ChatGPT in your browser, so the `chatgpt` option uses your plan instead of an API key.

    Raises:
        HeinzelError: If OpenAI's client is missing, or the sign-in fails, is declined or takes too long.
    """
    return access.login(progress)


@module.health
def access(service: AccessService) -> list[Check]:
    """The AI access in use: installed and signed in, and the agent runner it names is the one bound."""
    return service.checks()


__all__ = ["AccessList", "AccessOption", "AccessService", "AccessSettings", "AccessTestReport", "AccessUse", "module"]
