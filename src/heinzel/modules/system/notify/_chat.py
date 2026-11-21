"""Notices also in chat: the self channel as an extra place a notice reaches you, when you switch it on (ADR 0160).

What pops up in the app (or natively) is also sent to your own direct messages through the bound chat adapter's
`notify_self`: the same notices, at the same time, under the same policy (kinds, quiet mode, working hours, the rate
limit). It never replaces the app: the list keeps every notice, and a failure here never stops one from popping up.

Each notice is sent once. It is claimed in a small ledger under a lock before the adapter is called, so the web
app's process and the daemon's tick never both send it; a failed send is logged, not retried, and a claim whose process
ended mid-send (a tick that finished first) is taken over by the next sender. A Heinzel's notices
share one thread per Heinzel chat, forgotten once that chat no longer exists. Only notices that became due within
`WINDOW` are sent, so switching it on doesn't replay a backlog.
"""

import logging
import os
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from heinzel import Notice, NoticeKind, clean_text
from heinzel.kernel._logging import get_logger, log_event
from heinzel.kernel.session._self_chat import SelfChat, SelfChatLedger, process_alive

_EVENTS = get_logger("system.notify")
SENDING = "sending:"
"""A notice claimed by the process whose id follows; if that process ended mid-send, the next sender takes it over."""
FAILED = "failed"
"""A notice the adapter refused: logged, not retried."""
WINDOW = timedelta(hours=1)
"""Only notices that became due this recently go to chat."""
KIND_LABEL: dict[NoticeKind, str] = {
    NoticeKind.DRAFT: "Draft",
    NoticeKind.FAILED: "Failed run",
    NoticeKind.DOCTOR: "Health",
    NoticeKind.RELEASE: "Release",
    NoticeKind.PLUGIN: "Plugin notice",
    NoticeKind.TEST: "Test",
    NoticeKind.HEINZEL: "Heinzel",
    NoticeKind.CALENDAR: "Calendar",
}
"""The context label of a notice no Heinzel sent: where it comes from, in a word or two."""


def notice_ledger(directory: Path) -> SelfChatLedger:
    """Which notices went to chat (`sent`), and the thread of each Heinzel chat (`threads`)."""
    return SelfChatLedger(directory, "notices", ("sent", "threads"))


CHAT_ROUTE = "/heinzel?chat="
"""How a Heinzel chat's notices are routed in the web app; the rest of the route is the chat's id."""


def forget_chats(ledger: SelfChatLedger, listed: Callable[[], set[str]]) -> int:
    """Drop the threads of Heinzel chats `listed()` (those that exist) no longer lists; other threads stay.

    Only threads kept before the chats are listed can go, so a chat another process creates meanwhile keeps its own;
    nothing is written when nothing goes. How many went.
    """
    if not ledger.path.exists():
        return 0
    with ledger.locked() as state:
        kept = {k for k in state["threads"] if k.startswith(CHAT_ROUTE)}
    if not kept:
        return 0
    chats = listed()
    gone = {k for k in kept if k[len(CHAT_ROUTE) :] not in chats}
    if gone:
        with ledger.locked() as state:
            for key in gone:
                state["threads"].pop(key, None)
    return len(gone)


def chat_text(notice: Notice, url: str | None) -> str:
    """A notice as one chat message: its title, its body, and where to open it in the app or a terminal."""
    lines = [f"*{notice.title}*"]
    if notice.body:
        lines.append(notice.body)
    lines.append(f"Open in Heinzel: {url}" if url else f"In a terminal: `{notice.command}`")
    return "\n".join(lines)


def chat_context(notice: Notice) -> str:
    """Who the notice comes from: the Heinzel that sent it, else its kind."""
    return notice.helper.name if notice.helper is not None else KIND_LABEL.get(notice.kind, "Heinzel")


def send(
    notices: list[Notice],
    channel: SelfChat,
    ledger: SelfChatLedger,
    now: datetime,
    url: Callable[[Notice], str | None],
) -> int:
    """Send the notices that are due, recent and not sent yet; how many went out. Never raises for a send.

    Each is claimed in the ledger before the adapter is called; after the first failure the rest wait for the next
    call, as the adapter would most likely refuse them too.
    """
    sent = 0
    mine = SENDING + str(os.getpid())
    for notice in notices:
        if not (notice.popup and notice.resolved_at is None and now - WINDOW <= notice.due <= now):
            continue
        if notice.expires is not None and now >= notice.expires:
            continue
        thread_key = notice.route if notice.helper is not None else None
        with ledger.locked() as state:
            claim = state["sent"].get(notice.id)
            if claim is not None and not (claim.startswith(SENDING) and not process_alive(claim[len(SENDING) :])):
                continue  # sent, failed, or being sent by a process that still runs
            state["sent"][notice.id] = mine
            thread = state["threads"].get(thread_key) if thread_key else None
        try:
            message = channel.notify(chat_text(notice, url(notice)), thread=thread, context=chat_context(notice))
        except Exception as exc:  # noqa: BLE001 - isolation boundary: chat never costs a notice
            with ledger.locked() as state:
                state["sent"][notice.id] = FAILED
            log_event(
                _EVENTS,
                "notify.chat.failed",
                level=logging.WARNING,
                notice_id=notice.id,
                adapter=channel.adapter,
                problem=type(exc).__name__,
                detail=" ".join(clean_text(str(exc)).split())[:200],
            )
            return sent
        with ledger.locked() as state:
            state["sent"][notice.id] = now.isoformat()
            if thread_key and thread is None:
                state["threads"].setdefault(thread_key, message.thread)
        sent += 1
    return sent
