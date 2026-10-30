"""Errors that heads present to people instead of a traceback."""


class HeinzelError(Exception):
    """An expected failure with a message meant for the person or agent using Heinzelmännchen.

    Raise it from services and operations. The CLI head prints the message and exits with code 1; other heads map it
    to their own error shape. Anything else that escapes an operation is a bug and surfaces as one.
    """


class SubscriptionUsageLimitError(HeinzelError):
    """A redacted, fixed classification of an observed subscription limit."""

    message = (
        "Claude Code reported its subscription usage limit. Check your provider settings; "
        "this turn remains uncertain and will not retry automatically."
    )

    def __init__(self) -> None:
        super().__init__(self.message)
