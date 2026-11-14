"""The self channel: the bound chat adapter's direct messages to the user, for hz's notices and questions (ADR 0160).

A chat adapter may serve `Chat.notify_self` and `Chat.ask_self` besides, or instead of, `messages` and `post`. When the
user switches it on, hz also sends its notices there and also asks there what a Heinzel asks in the app. It is an extra
place, never a replacement: the app shows everything as before, and nothing sent here is lost from it.

`SelfChat` is hz's own trusted view of that adapter, as `Kernel.ai_models` is for the AI engine: it is not a requester's
handle, and modules never get one. It bounds what goes out (one-line context, at most `SELF_OPTIONS` short options,
text cleaned of control and invisible characters) and checks what comes back: an answer's option must be one that was
offered, and every string is cleaned. What the user wrote is still untrusted input, like anything typed in a head.
"""

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, cast

from heinzel.kernel._errors import HeinzelError, NotSupportedError
from heinzel.kernel._protocols import (
    SELF_CONTEXT_LENGTH,
    SELF_OPTION_LENGTH,
    SELF_OPTIONS,
    Chat,
    SelfAnswer,
    SelfMessage,
)
from heinzel.kernel._text import clean_text

CAPABILITY = "chat"
SelfUse = Literal["notifications", "questions"]
"""What the self channel carries, each switched on by itself: hz's notices, and a Heinzel's questions."""
METHOD: dict[SelfUse, str] = {"notifications": "notify_self", "questions": "ask_self"}
"""The `Chat` method each use needs."""
MAX_SELF_TEXT = 3000
"""Longest text hz sends in one self message; longer text is cut with an ellipsis and the app keeps the whole."""
MAX_TIMEOUT = 3600.0
"""Longest a question waits in chat, in seconds."""


@dataclass(frozen=True, slots=True)
class SelfChat:
    """The bound chat adapter's self channel, as far as it serves one; built only for hz's trusted callers."""

    adapter: str
    """The adapter's name, such as `slack`: what settings show as the chat that will be used."""
    notifies: bool
    """Whether it serves `notify_self`."""
    asks: bool
    """Whether it serves `ask_self`."""
    build: Callable[[], object]
    """The adapter itself, built on first use."""

    def serves(self, use: SelfUse) -> bool:
        """Whether it can carry `use`."""
        return self.notifies if use == "notifications" else self.asks

    def notify(self, text: str, *, thread: str | None = None, context: str | None = None) -> SelfMessage:
        """Send `text` to the user's own direct messages; what was sent, with the thread to reply in.

        Raises:
            NotSupportedError: If the adapter doesn't serve `notify_self`.
            HeinzelError: If the adapter's answer isn't a `SelfMessage`.
        """
        if not self.notifies:
            raise NotSupportedError(CAPABILITY, "notify_self")
        adapter = cast("Chat", self.build())
        sent: object = adapter.notify_self(self_text(text), thread=_handle(thread), context=self_context(context))
        if not isinstance(sent, SelfMessage):
            msg = f"The chat adapter {self.adapter} answered notify_self with something other than a SelfMessage."
            raise HeinzelError(msg)
        return SelfMessage(id=clean_text(sent.id), thread=clean_text(sent.thread), url=sent.url)

    def ask(  # noqa: PLR0913 - the question and its keyword-only knobs, as `Chat.ask_self` takes them
        self,
        question: str,
        *,
        options: Sequence[str] = (),
        timeout: float,
        thread: str | None = None,
        context: str | None = None,
        cancel: threading.Event | None = None,
    ) -> SelfAnswer:
        """Ask in the user's own direct messages and wait, in the caller's thread; never call it on an event loop.

        Returns the answer with `option` checked against `options`: an option that wasn't offered is refused.

        Raises:
            NotSupportedError: If the adapter doesn't serve `ask_self`.
            HeinzelError: If the options or timeout are out of bounds, or the answer isn't a valid `SelfAnswer`.
        """
        if not self.asks:
            raise NotSupportedError(CAPABILITY, "ask_self")
        offered = self_options(options)
        if not 1 <= timeout <= MAX_TIMEOUT:
            msg = f"A question waits in chat between 1 and {MAX_TIMEOUT:.0f} seconds."
            raise HeinzelError(msg)
        adapter = cast("Chat", self.build())
        answer: object = adapter.ask_self(
            self_text(question),
            options=offered,
            timeout=timeout,
            thread=_handle(thread),
            context=self_context(context),
            cancel=cancel,
        )
        return checked_answer(answer, offered, self.adapter)


def checked_answer(answer: object, offered: Sequence[str], adapter: str) -> SelfAnswer:
    """`answer` cleaned, with its option one of `offered`.

    Raises:
        HeinzelError: If it isn't a `SelfAnswer`, or names an option that wasn't offered.
    """
    if not isinstance(answer, SelfAnswer):
        msg = f"The chat adapter {adapter} answered ask_self with something other than a SelfAnswer."
        raise HeinzelError(msg)
    if answer.timed_out:
        return SelfAnswer(thread=_cleaned(answer.thread), timed_out=True)
    if answer.option is not None and answer.option not in offered:
        msg = f"The chat adapter {adapter} answered with an option that wasn't offered."
        raise HeinzelError(msg)
    text = clean_text(answer.text).strip() or (answer.option or "")
    return SelfAnswer(thread=_cleaned(answer.thread), text=text, option=answer.option)


def self_text(text: str) -> str:
    """`text` as hz sends it: cleaned of control and invisible characters, at most `MAX_SELF_TEXT` characters.

    Raises:
        HeinzelError: If nothing is left to send.
    """
    cleaned = clean_text(text).strip()
    if not cleaned:
        msg = "A self message needs text."
        raise HeinzelError(msg)
    return cleaned if len(cleaned) <= MAX_SELF_TEXT else cleaned[: MAX_SELF_TEXT - 1].rstrip() + "…"


def self_context(context: str | None) -> str | None:
    """A context label on one line, at most `SELF_CONTEXT_LENGTH` characters; None when empty."""
    if context is None:
        return None
    line = " ".join(clean_text(context).split())
    return line[:SELF_CONTEXT_LENGTH] or None


def self_options(options: Sequence[str]) -> tuple[str, ...]:
    """The options as offered: each on one line, distinct, non-empty, short, at most `SELF_OPTIONS`.

    Raises:
        HeinzelError: If there are too many, or one is empty, too long or repeated.
    """
    if isinstance(options, str):
        msg = "Options are a list of labels, not one string."
        raise HeinzelError(msg)
    offered = tuple(" ".join(clean_text(option).split()) for option in options)
    if len(offered) > SELF_OPTIONS:
        msg = f"A question offers at most {SELF_OPTIONS} options."
        raise HeinzelError(msg)
    if any(not option or len(option) > SELF_OPTION_LENGTH for option in offered) or len(set(offered)) != len(offered):
        msg = f"Each option is a distinct label of 1 to {SELF_OPTION_LENGTH} characters."
        raise HeinzelError(msg)
    return offered


def _handle(thread: str | None) -> str | None:
    return None if thread is None else (clean_text(thread).strip() or None)


def _cleaned(value: str | None) -> str | None:
    return None if value is None else clean_text(value)
