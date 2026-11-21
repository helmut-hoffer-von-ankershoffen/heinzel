"""The receiving end of the browser relay: this process holds the desktop's bridge and runs others' calls (ADR 0162).

A call arrives with a kind, a chat session, a tool name and arguments, nothing else. For a Heinzel this process
builds the chat's own tool set for that session, finds the browser tool by name and runs its `ChatTool.call`: the
same one policy as in-process, so a commit is proposed here and the sender only gets the text. For stdio MCP the
agent is always `local`, an outside agent; the sender cannot name another. Every other tool and name is refused.
"""

import logging
from threading import Lock

from pydantic import JsonValue

from heinzel import HeinzelError, Kernel
from heinzel.kernel import CurrentUser
from heinzel.kernel._browser import BROWSER_MODULE, BrowserCaller, PageImage, browser_caller
from heinzel.kernel._browser_relay import BrowserRelay
from heinzel.kernel._exposure import exposed
from heinzel.kernel._logging import log_event
from heinzel.kernel._text import redact_emails
from heinzel.kernel.session._chat_services import McpChatSessions
from heinzel.kernel.session._chat_tools import AUTHENTICATION, HEAD, ChatTool, ChatToolError, chat_tools

log = logging.getLogger(__name__)
REFUSED = "This is not a browser tool of this chat."
NO_TURN = "This chat has no running turn; a Heinzel uses the browser only while it answers."


def handle(kernel: Kernel, kind: str, session: str, tool: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Run one relayed browser call here and answer `text`, `image` or `result`, or `error`."""
    try:
        if kind == "heinzel":
            return _for_heinzel(kernel, session, tool, arguments)
        if kind == "external":
            return _for_local_agent(kernel, tool, arguments)
    except ChatToolError as exc:
        return {"error": str(exc)}
    except HeinzelError as exc:
        return {"error": redact_emails(str(exc))}
    return {"error": REFUSED}


def _for_heinzel(kernel: Kernel, session: str, tool: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
    found = [item for item in chat_tools(kernel, session) if item.name == tool]  # refuses an unknown session
    if len(found) != 1 or found[0].item.record.qualname != BROWSER_MODULE:
        return {"error": REFUSED}
    # The sender names a session; nothing proves it is that session's agent. So the session must be one somebody
    # is answering right now: outside a running turn no Heinzel exists that could be calling.
    user = kernel.resolve(CurrentUser)
    with user.using(None), user.local_read(AUTHENTICATION):
        if not kernel.resolve(McpChatSessions).turn_running(session):
            return {"error": NO_TURN}
    result = found[0].call(arguments)
    if isinstance(result, PageImage):
        return {"image": result.model_dump(mode="json")}
    return {"text": str(result)}


def _for_local_agent(kernel: Kernel, tool: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
    found = [item for item in exposed(kernel, HEAD) if item.name == tool and item.record.qualname == BROWSER_MODULE]
    if len(found) != 1:
        return {"error": REFUSED}
    [item] = found
    checked = ChatTool(kernel, item, "").arguments(arguments)  # only the tool's own parameters, of the right kind
    user = kernel.resolve(CurrentUser)
    with (
        user.using(None),
        user.local_read(AUTHENTICATION),
        browser_caller(BrowserCaller(actor="local", kind="external")),
    ):
        result = kernel.invoke(item.operation, **checked)
    return {"result": result.model_dump(mode="json")}


class RelayHost:
    """Opens the relay while a desktop is connected to this process's bridge, and closes it when it leaves."""

    def __init__(self, kernel: Kernel) -> None:
        """Serve calls with `kernel`'s own tools."""
        self._kernel = kernel
        self._lock = Lock()
        self._relay: BrowserRelay | None = None

    def follow(self, connected: bool) -> None:
        """Open on the first desktop, close on the last; a relay that cannot open leaves this process working."""
        with self._lock:
            if connected and self._relay is None:
                try:
                    self._relay = BrowserRelay(self._kernel.settings.state_dir, self._handle)
                except (OSError, HeinzelError) as exc:
                    log_event(log, "browser.relay.unavailable", level=logging.WARNING, error=str(exc))
            elif not connected and self._relay is not None:
                self._relay.close()
                self._relay = None

    def _handle(self, kind: str, session: str, tool: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return handle(self._kernel, kind, session, tool, arguments)
