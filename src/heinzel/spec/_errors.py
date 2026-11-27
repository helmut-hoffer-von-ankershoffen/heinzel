"""Errors that heads present to people instead of a traceback."""

# Class-qualified data must remain readable by the published releases.
_PUBLISHED_MODULE = "heinzel.kernel._errors"


class HeinzelError(Exception):
    """An expected failure with a message meant for the person or agent using Heinzelmännchen.

    Raise it from services and operations. The CLI head prints the message and exits with code 1; other heads map it
    to their own error shape. Anything else that escapes an operation is a bug and surfaces as one.
    """

    __module__ = _PUBLISHED_MODULE


class SubscriptionUsageLimitError(HeinzelError):
    """A redacted, fixed classification of an observed subscription limit."""

    __module__ = _PUBLISHED_MODULE

    message = (
        "Claude Code reported its subscription usage limit. Check your provider settings; "
        "this turn remains uncertain and will not retry automatically."
    )

    def __init__(self) -> None:
        super().__init__(self.message)


class NotSupportedError(HeinzelError):
    """An adapter can't do what one method of its capability asks, by design, not by failure (ADR 0159).

    A connector built on a vendor's API or an MCP server may lack one operation, such as attaching a file to an issue
    or replacing a document section. The adapter raises this error from that method, or lists the method in its
    `unsupported` class attribute so hz refuses the call before reaching it. Every head shows it as an answer, not a
    crash: the CLI prints it and exits 1, REST and the web app answer `400` with `code: not_supported`, a chat tool
    tells the model it can't do that here. Nothing is retried, and the conformance kit accepts it for any method.
    """

    __module__ = _PUBLISHED_MODULE

    def __init__(self, capability: str, method: str, reason: str = "") -> None:
        """Say which method of which capability the adapter doesn't support, and optionally why, in a few words."""
        self.capability = capability
        self.method = method
        self.reason = reason
        why = f": {reason}" if reason else ""
        super().__init__(f"This {capability} connector can't {method.replace('_', ' ')}{why}.")
