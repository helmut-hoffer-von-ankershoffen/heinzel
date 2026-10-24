"""Errors that heads present to people instead of a traceback."""


class HeinzelError(Exception):
    """An expected failure with a message meant for the person or agent using Heinzelmännchen.

    Raise it from services and operations. The CLI head prints the message and exits with code 1; other heads map it
    to their own error shape. Anything else that escapes an operation is a bug and surfaces as one.
    """
