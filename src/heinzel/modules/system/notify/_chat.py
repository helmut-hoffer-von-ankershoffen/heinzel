"""Notices also in chat: the self channel as an extra place a notice reaches you, when you switch it on (ADR 0160).

What pops up in the app (or natively) is also sent to your own direct messages through the bound chat adapter's
`notify_self`: the same notices, at the same time, under the same policy (kinds, quiet mode, working hours, the rate
limit). It never replaces the app: the list keeps every notice, and a failure here never stops one from popping up.

Each notice is sent at most once. It is claimed in a small ledger under a lock before the adapter is called, so the
web app's process and the daemon's tick never both send it; a failed send is logged, not retried. A Heinzel's notices
share one thread per Heinzel chat. Only notices that became due within `WINDOW` are sent, so switching it on doesn't
replay a backlog.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from heinzel import Notice, NoticeKind, clean_text
from heinzel.kernel._logging import get_logger, log_event
from heinzel.kernel._self_chat import SelfChat, SelfChatLedger

_EVENTS = get_logger("system.notify")
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
    for notice in notices:
        if not (notice.popup and notice.resolved_at is None and now - WINDOW <= notice.due <= now):
            continue
        if notice.expires is not None and now >= notice.expires:
            continue
        thread_key = notice.route if notice.helper is not None else None
        with ledger.locked() as state:
            if notice.id in state["sent"]:
                continue
            state["sent"][notice.id] = now.isoformat()
            thread = state["threads"].get(thread_key) if thread_key else None
        try:
            message = channel.notify(chat_text(notice, url(notice)), thread=thread, context=chat_context(notice))
        except Exception as exc:  # noqa: BLE001 - isolation boundary: chat never costs a notice
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
        if thread_key and thread is None:
            with ledger.locked() as state:
                state["threads"].setdefault(thread_key, message.thread)
        sent += 1
    return sent
