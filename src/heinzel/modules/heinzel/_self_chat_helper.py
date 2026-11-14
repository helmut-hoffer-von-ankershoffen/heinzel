"""The detached helper that asks a scheduled turn's question in chat (ADR 0160); private, no command of its own.

The daemon's tick ends long before an ask in chat may be answered, so its question relay starts this module as a process
of its own (`python -P -m heinzel.modules.heinzel._self_chat_helper SESSION TURN`, in the relay's private directory),
with the tick's environment: the same profile, home and state directory. It boots Heinzel as the launchd tick does (from
the home directory) and runs the same relay, with the same switch, wait, caps and answer path, under the relay's own
`self-chat` identity. It is a short-lived process too: a question its own answer turn asks gets a helper of its own.

It never outlives `lifetime(settings)`: the wait in chat, the grace an adapter has, one answer turn and a margin; a
watchdog ends it then, whatever hangs. A helper that ends that way or crashes leaves its live entry, which the next ask
says is still open.
"""

import os
import re
import sys
import threading
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from heinzel.kernel._boot import boot
from heinzel.modules.heinzel._self_chat_questions import QuestionSettings, lifetime, short_lived_process, user_settings
from heinzel.modules.heinzel._session_models import SessionId
from heinzel.modules.heinzel._user_chat import UserChatService

_SESSION = TypeAdapter(SessionId)
_TURN = re.compile(r"interactive-[0-9a-f]{64}")
TIMED_OUT = 124
"""The exit code of a helper its watchdog ended."""
_exit = os._exit


def _valid(session: str, turn: str) -> bool:
    try:
        _SESSION.validate_python(session)
    except ValidationError:
        return False
    return _TURN.fullmatch(turn) is not None


def _end() -> None:
    sys.stderr.write("hz self-chat helper: ended by its watchdog\n")
    sys.stderr.flush()
    _exit(TIMED_OUT)


def watchdog(seconds: float) -> threading.Timer:
    """End this process after `seconds`, whatever it is waiting for."""
    timer = threading.Timer(seconds, _end)
    timer.daemon = True
    timer.start()
    return timer


def main(argv: list[str] | None = None) -> int:
    """Ask the question of one finished turn in chat and act on the answer; 2 for arguments that aren't two ids."""
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2 or not _valid(*arguments):  # noqa: PLR2004 - session and turn
        return 2
    session, turn = arguments
    try:
        settings = user_settings()
    except (OSError, ValueError):
        settings = QuestionSettings(chat_wait_minutes=60)  # the longest wait: unreadable settings never cut it short
    timer = watchdog(lifetime(settings))
    try:
        relay = boot(cwd=Path.home()).resolve(UserChatService).questions  # the launchd tick's working directory
        if relay is not None:
            with short_lived_process():
                relay.run_detached(session, turn)
    finally:
        timer.cancel()
    return 0


if __name__ == "__main__":
    sys.exit(main())
