"""A made-up chat connector that can only message its owner (ADR 0160): a fixture for browser and CLI tests.

It keeps what it was asked to send in the file `HZ_SELF_CHAT_DEMO_INBOX` names, one JSON line each, so a test sees what
reached the owner's direct messages; nothing leaves the machine. A question waits until cancelled or out of time.
"""

import json
import os
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from heinzel import Module, SelfAnswer, SelfMessage

module = Module("chat-demo", "A made-up chat that can only message its owner.")


def _keep(entry: dict[str, object]) -> int:
    inbox = Path(os.environ["HZ_SELF_CHAT_DEMO_INBOX"])
    with inbox.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    return sum(1 for _ in inbox.open(encoding="utf-8"))


@module.provides("chat", modes={"read", "write"}, name="demo-chat")
class DemoChat:
    """The owner's own direct messages, kept in a file."""

    unsupported = frozenset({"messages", "post"})

    def notify_self(self, text: str, *, thread: str | None = None, context: str | None = None) -> SelfMessage:
        """Keep the message."""
        number = _keep({"text": text, "thread": thread, "context": context})
        return SelfMessage(id=f"m{number}", thread=thread or f"m{number}")

    def ask_self(  # noqa: PLR0913 - the protocol's shape
        self,
        question: str,
        *,
        options: Sequence[str] = (),
        timeout: float,
        thread: str | None = None,
        context: str | None = None,
        cancel: threading.Event | None = None,
    ) -> SelfAnswer:
        """Keep the question and wait: nobody answers here."""
        _keep({"question": question, "options": list(options), "thread": thread, "context": context})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not (cancel is not None and cancel.is_set()):
            time.sleep(0.2)
        return SelfAnswer(timed_out=True, thread=thread)
