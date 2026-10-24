"""The MCP head: every operation becomes a tool for MCP clients such as coding agents (which can also use the CLI).

The tool's input schema comes from the operation's parameters (`Annotated[T, Field(description=...)]`), its
description from the docstring, its output schema from the return model, and `read_only` from the operation's flag.

With `chat=<session>` (`hz system serve mcp --chat <session>`, started over stdio by the agent of a chat, ADR 0045)
it serves the same tools to that one chat, and enforces the chat's rule in code: a read-only operation runs and
returns its result as JSON without email addresses; any other operation does not run. It is handed to the
`ActionGate` as a proposal, and the tool returns its action id: only the person's `hz chat confirm` (CLI or web head,
never MCP) runs it. Tools follow the MCP head's own exposure, so what a UI hides from beginners is still a tool here.
"""

import inspect
import json
import logging
import shlex
from collections.abc import Callable
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic_core import to_jsonable_python

from heinzel import __version__
from heinzel.heads._exposure import MCP_HTTP, Exposed, exposed
from heinzel.kernel import ActionGate, HeinzelError, Kernel, ProposedCall, boot, redact_emails

HEAD = "mcp"
log = logging.getLogger(__name__)
INSTRUCTIONS = (
    "Heinzelmännchen helps product development teams with the busywork of running a team. Tools are named after "
    "their command path: `system_doctor` is `hz system doctor`. Start with `system_info` or `system_doctor`; tools "
    "marked as not read-only change state, so ask the user before calling them unless they asked for exactly that."
)
CHAT_INSTRUCTIONS = (
    "Heinzelmännchen's operations for one chat. Tools are named after their command path: `system_doctor` is "
    "`hz system doctor`. Read-only tools run and return JSON. Any other tool does not run: it is proposed to the "
    "person, who confirms or declines it in the chat, and the call returns its action id."
)
RESULT_LIMIT = 20000
"""Characters of a tool result an agent in a chat gets; the rest is cut, so one call can't fill its context."""

Transport = Literal["stdio", "streamable-http"]


ChatTools = Literal["everyday", "all"]


def build(
    kernel: Kernel, transport: Transport = "stdio", *, chat: str | None = None, chat_tools: ChatTools = "everyday"
) -> MCPServer:
    """An MCP server with one tool per exposed operation; for one chat session's agent when `chat` is given.

    A chat's agent gets the everyday operations only (ADR 0041) unless `chat_tools` is `all`: every tool's schema is
    sent with every message, so 24 tools instead of 75 halve what a short message costs (ADR 0057).

    Over streamable HTTP, which has no authentication, the operations restricted to stdio (`mcp-stdio` in their
    `heads`, such as installing plugins) are left out.
    """
    server = MCPServer(
        name="heinzel",
        title="🧝 Heinzelmännchen",
        version=__version__,
        instructions=INSTRUCTIONS if chat is None else CHAT_INSTRUCTIONS,
    )
    for item in exposed(kernel, HEAD if transport == "stdio" else MCP_HTTP):
        if chat is not None and chat_tools == "everyday" and item.operation.audience != "everyday":
            continue
        server.add_tool(
            tool(kernel, item) if chat is None else chat_tool(kernel, item, chat),
            name=item.name,
            description=item.operation.summary,
            annotations=ToolAnnotations(
                read_only_hint=item.operation.read_only, destructive_hint=not item.operation.read_only
            ),
        )
    return server


def tool(kernel: Kernel, item: Exposed) -> Callable[..., Any]:
    """A function with the operation's public signature that runs it through the kernel."""

    def run(**kwargs: Any) -> Any:  # noqa: ANN401 - whatever the operation returns
        try:
            return kernel.invoke(item.operation, **kwargs)
        except HeinzelError as exc:
            raise ToolError(str(exc)) from exc

    return _signed(run, item, item.returns)


def chat_tool(kernel: Kernel, item: Exposed, session: str) -> Callable[..., str]:
    """The tool of one chat session: runs a read-only operation, and only proposes any other (ADR 0045)."""

    def run(**kwargs: Any) -> str:  # noqa: ANN401 - the operation's own parameters
        try:
            if item.operation.read_only:
                result = kernel.invoke(item.operation, **kwargs)
                # Redacted before it is cut, so no address is cut into something the pattern misses.
                text = redact_emails(json.dumps(to_jsonable_python(result, fallback=str), ensure_ascii=False))
                return text[:RESULT_LIMIT] + ("…(cut)" if len(text) > RESULT_LIMIT else "")
            arguments = to_jsonable_python(kwargs, fallback=str)
            call = ProposedCall(
                session=session,
                module=item.record.qualname,
                operation=item.operation.name,
                tool=item.name,
                arguments=arguments,
                command=command_line(item, arguments),
            )
            action = kernel.resolve(ActionGate).propose(call)
        except HeinzelError as exc:
            raise ToolError(redact_emails(str(exc))) from exc
        except Exception as exc:  # a bug's message may hold anything: the agent gets its type only
            log.exception("Chat tool %s failed", item.name)
            msg = f"{item.name} failed with a bug ({type(exc).__name__}); the person can see the log."
            raise ToolError(msg) from exc
        return redact_emails(
            f"Not run: `{call.command}` changes things, so it waits for the person's confirmation in the chat "
            f"(action {action}). Tell them briefly what it will do and why, then stop; don't call it again."
        )

    return _signed(run, item, str)


def command_line(item: Exposed, arguments: dict[str, Any]) -> str:
    """The call as the person would type it: `hz system config set --key a.b --value 1`."""
    words = ["hz", *item.path]
    for name, value in arguments.items():
        if value is None:
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        words += [f"--{name.replace('_', '-')}", shlex.quote(text)]
    return " ".join(words)


def _signed[F: Callable[..., Any]](run: F, item: Exposed, returns: object) -> F:
    run.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [p.replace(kind=inspect.Parameter.KEYWORD_ONLY) for p in item.parameters], return_annotation=returns
    )
    run.__doc__ = inspect.getdoc(item.operation.func)
    run.__name__ = item.name
    return run


def serve(
    transport: Transport = "stdio",
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    chat: str | None = None,
    chat_tools: ChatTools = "everyday",
) -> None:
    """Boot the kernel and serve its operations over `transport` until interrupted; for one chat over stdio only.

    Raises:
        HeinzelError: If `chat` is given with another transport than stdio.
    """
    if chat is not None and transport != "stdio":
        msg = "A chat's tools are served over stdio only, never on a port."
        raise HeinzelError(msg)
    server = build(boot(), transport, chat=chat, chat_tools=chat_tools)
    if transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=host, port=port)
