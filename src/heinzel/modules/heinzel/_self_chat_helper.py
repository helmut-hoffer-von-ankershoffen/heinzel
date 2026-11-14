"""The detached helper that asks a scheduled turn's question in chat (ADR 0160); private, no command of its own.

The daemon's tick ends long before an ask in chat may be answered, so its question relay starts this module as a process
of its own (`python -m heinzel.modules.heinzel._self_chat_helper SESSION TURN`), with the tick's environment: the same
profile, home and state directory. It boots Heinzel as the tick does and runs the same relay, with the same switch,
wait, caps and answer path, under the relay's own `self-chat` identity. A helper that crashes leaves its live entry,
which the next ask says is still open.
"""

import sys

from heinzel.kernel._boot import boot
from heinzel.modules.heinzel._user_chat import UserChatService

_MAX_ID = 512


def _valid(value: str) -> bool:
    return 0 < len(value) <= _MAX_ID and value.isprintable() and not value.startswith("-")


def main(argv: list[str] | None = None) -> int:
    """Ask the question of one finished turn in chat and act on the answer; 2 for arguments that aren't two ids."""
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2 or not all(_valid(value) for value in arguments):  # noqa: PLR2004 - session and turn
        return 2
    session, turn = arguments
    relay = boot().resolve(UserChatService).questions
    if relay is not None:
        relay.run_detached(session, turn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
