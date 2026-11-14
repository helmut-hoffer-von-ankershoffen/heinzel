"""The engine runner's conversation memory: one Pydantic AI message history per chat session (ADR 0159).

A chat on hz's own engine continues its session the way Pydantic AI models one: each turn runs with the
`message_history` of the turns before and adds its new messages. The history is kept per chat session, as
`ModelMessagesTypeAdapter` JSON inside that chat's own private folder (`<state_dir>/chat/<session>/`, which the
chat creates): so deleting or pruning the chat deletes its history too, and no history outlives its chat. It is
owner-only (file 0600), written atomically (a temporary file renamed into place), never followed through a
symlink, never written when the chat's folder is gone, and read back after a restart. A history that can't be
read is a fresh start, never an error.

It stays within what the model can read: with a known context window, whole turns are dropped from the start until
the estimated size (four characters a token) fits `CONTEXT_SHARE` of it; without one, at most `MAX_MESSAGES`
messages are kept. A turn that stopped (its timeout, or a cancel) keeps what it had said so far, marked stopped.
"""

import contextlib
import dataclasses
import itertools
import json
import os
import re
import secrets
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:  # Pydantic AI loads only when a run uses the history, not when hz starts
    from pydantic_ai.messages import ModelMessage

MAX_MESSAGES = 200
"""The most messages kept for a model whose context window is unknown."""
CONTEXT_SHARE = 0.6
"""The share of a known context window the history may fill; the rest is the new turn, its tools and the answer."""
CHARS_PER_TOKEN = 4
MAX_FILE_BYTES = 32 * 1024 * 1024
SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
STOPPED = "[stopped: {why}]"
_FORMAT = 1


def session_of(commands: list[list[str]]) -> str | None:
    """The chat session a run belongs to: the `--chat <session>` argument of hz's chat MCP server, if any."""
    for command in commands:
        for flag, value in itertools.pairwise(command):
            if flag == "--chat" and SESSION_PATTERN.fullmatch(value):
                return value
    return None


def _turns(messages: "list[ModelMessage]") -> "list[list[ModelMessage]]":
    """The messages split into turns, each starting at a request that carries a user prompt."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart  # noqa: PLC0415 - only during a run

    turns: list[list[ModelMessage]] = []
    for message in messages:
        starts = isinstance(message, ModelRequest) and any(isinstance(p, UserPromptPart) for p in message.parts)
        if starts or not turns:
            turns.append([])
        turns[-1].append(message)
    return turns


def capped(messages: "list[ModelMessage]", context_window: int | None) -> "list[ModelMessage]":
    """`messages` without their oldest whole turns, until they fit the window's share or `MAX_MESSAGES`."""
    from pydantic_ai.messages import ModelMessagesTypeAdapter  # noqa: PLC0415 - only during a run

    turns = _turns(messages)

    def fits() -> bool:
        kept = [m for turn in turns for m in turn]
        if context_window is None:
            return len(kept) <= MAX_MESSAGES
        size = len(ModelMessagesTypeAdapter.dump_json(kept)) / CHARS_PER_TOKEN
        return size <= context_window * CONTEXT_SHARE

    while len(turns) > 1 and not fits():
        turns.pop(0)
    return [m for turn in turns for m in turn]


def stopped(messages: "list[ModelMessage]", partial: str, why: str) -> "list[ModelMessage]":
    """The history of a turn that stopped: what it had, ending in what it had answered so far, marked stopped.

    A trailing response with tool calls that never got their results is dropped, so the history stays one a model
    accepts. The response being written when the turn stopped is kept with the marker after it; when the stop came
    before any of it was recorded, the streamed `partial` text answers the last request instead.
    """
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart  # noqa: PLC0415 - only during a run

    kept = list(messages)
    while kept and isinstance(kept[-1], ModelResponse):
        if not any(isinstance(p, ToolCallPart) for p in kept[-1].parts):
            break
        kept.pop()
    marker = STOPPED.format(why=why)
    if kept and isinstance(kept[-1], ModelResponse):
        kept[-1] = dataclasses.replace(kept[-1], parts=[*kept[-1].parts, TextPart(marker)])
    elif kept:
        kept.append(ModelResponse([TextPart(f"{partial}\n\n{marker}" if partial else marker)]))
    return kept


FILE = "engine-history.json"
"""The history's file in a chat's folder."""


class SessionHistory:
    """The stored message histories of the engine's chat sessions, each in its chat's own folder."""

    def __init__(self, chats: Path) -> None:
        """Keep each history in `chats/<session>/`, the chat's folder; never create one."""
        self._chats = chats

    def _folder(self, session: str) -> Path:
        if not SESSION_PATTERN.fullmatch(session):
            msg = f"not a session id: {session!r}"
            raise ValueError(msg)
        return self._chats / session

    def _path(self, session: str) -> Path:
        return self._folder(session) / FILE

    def load(self, session: str) -> "tuple[list[ModelMessage], dict[str, Any]]":
        """The session's messages and the notes kept with them; empty when there are none or they can't be read."""
        from pydantic_ai.messages import ModelMessagesTypeAdapter  # noqa: PLC0415 - only during a run

        path = self._path(session)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        except OSError:
            return [], {}
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES or info.st_uid != os.getuid():
                return [], {}
            raw = stream.read(MAX_FILE_BYTES + 1)
        try:
            data = json.loads(raw)
            if data.get("format") != _FORMAT:
                return [], {}
            found = data.get("notes")
            notes: dict[str, Any] = cast("dict[str, Any]", found) if isinstance(found, dict) else {}
            return ModelMessagesTypeAdapter.validate_python(data["messages"]), notes
        except (ValueError, KeyError, TypeError, AttributeError):
            return [], {}

    def save(self, session: str, messages: "list[ModelMessage]", notes: dict[str, Any]) -> None:
        """Replace the session's history atomically, owner-only, in the chat's folder.

        Raises:
            NotADirectoryError: If the chat's folder is a symlink or not a folder.
            OSError: If the chat's folder is gone, or the file can't be written.
        """
        from pydantic_ai.messages import ModelMessagesTypeAdapter  # noqa: PLC0415 - only during a run

        folder = self._folder(session)
        if not stat.S_ISDIR(folder.lstat().st_mode):  # a deleted chat keeps no history; lstat raises when it's gone
            msg = f"the chat {session} has no folder"
            raise NotADirectoryError(msg)
        path = folder / FILE
        body = json.dumps(
            {
                "format": _FORMAT,
                "notes": notes,
                "messages": ModelMessagesTypeAdapter.dump_python(messages, mode="json"),
            },
            separators=(",", ":"),
        ).encode()
        temporary = folder / f".{FILE}.{secrets.token_hex(8)}.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            Path(temporary).replace(path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()

    def drop(self, session: str) -> None:
        """Forget the session's history."""
        with contextlib.suppress(FileNotFoundError):
            self._path(session).unlink()
