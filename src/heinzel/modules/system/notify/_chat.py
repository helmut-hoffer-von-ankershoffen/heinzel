"""Notices also in chat: the self channel as an extra place a notice reaches you, when you switch it on (ADR 0160).

What pops up in the app (or natively) is also sent to your own direct messages through the bound chat adapter's
`notify_self`: the same notices, at the same time, under the same policy (kinds, quiet mode, working hours, the rate
limit). It never replaces the app: the list keeps every notice, and a failure here never stops one from popping up.

Each notice is sent at most once. It is claimed in a small ledger under a lock before the adapter is called, so the
web app's process and the daemon's tick never both send it; a failed send is logged, not retried. A Heinzel's notices
share one thread per Heinzel chat. Only notices that became due within `WINDOW` are sent, so switching it on doesn't
replay a backlog.
"""

import fcntl
import json
import logging
import os
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from heinzel import Notice, NoticeKind, clean_text
from heinzel.kernel._logging import get_logger, log_event
from heinzel.kernel._self_chat import SelfChat

_EVENTS = get_logger("system.notify")
WINDOW = timedelta(hours=1)
"""Only notices that became due this recently go to chat."""
MAX_LEDGER = 500
"""Most notice ids and threads the ledger keeps; the oldest go first."""
PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600
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


class Ledger:
    """Which notices went to chat, and the thread of each Heinzel chat: `self-chat/notices.json`, private."""

    def __init__(self, directory: Path) -> None:
        """Keep it under `directory`; nothing is written until something is sent."""
        self.directory = directory
        self._file = directory / "notices.json"
        self._lock = directory / "notices.lock"

    @contextmanager
    def locked(self) -> Generator[dict[str, dict[str, str]]]:
        """The ledger under an exclusive lock; what the block leaves in it is written back.

        Yields:
            The ledger: `sent` notice ids with when they were claimed, and `threads` by Heinzel chat route.
        """
        self.directory.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR)
        descriptor = os.open(self._lock, os.O_WRONLY | os.O_CREAT | os.O_APPEND, PRIVATE_FILE)
        with os.fdopen(descriptor, "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                state = self._read()
                yield state
                self._write(state)
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _read(self) -> dict[str, dict[str, str]]:
        try:
            data: object = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        state: dict[str, dict[str, str]] = {"sent": {}, "threads": {}}
        if isinstance(data, dict):
            for part in state:
                value: object = data.get(part)
                if isinstance(value, dict):
                    state[part] = {str(k): str(v) for k, v in value.items()}
        return state

    def _write(self, state: dict[str, dict[str, str]]) -> None:
        bounded = {part: dict(list(entries.items())[-MAX_LEDGER:]) for part, entries in state.items()}
        partial = self._file.with_name(f".{self._file.name}.{os.getpid()}")
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(bounded, handle)
        partial.replace(self._file)


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
    ledger: Ledger,
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
