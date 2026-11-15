"""The engine's chat-session histories: kept within the model's reach, private, and never a crash (ADR 0159)."""

from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)

from heinzel.modules.ai.access import _engine_history
from heinzel.modules.ai.access._engine_history import SessionHistory, capped, session_of, stopped

pytestmark = pytest.mark.unit


def turn(n: int, size: int = 10) -> list[ModelMessage]:
    return [ModelRequest.user_text_prompt(f"q{n} " + "x" * size), ModelResponse([TextPart(f"a{n}")])]


def test_whole_turns_are_dropped_to_fit_the_window_or_the_message_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = turn(1, 4000) + turn(2, 4000) + turn(3, 40)
    huge = turn(1, 100_000)

    assert capped(messages, None) == messages
    assert capped(messages, 2000) == messages[4:]  # 60 % of 2000 tokens: only the last turn fits
    assert capped(messages, 4000) == messages[2:]
    assert capped(huge, 10) == huge  # the newest turn always stays
    monkeypatch.setattr(_engine_history, "MAX_MESSAGES", 4)
    assert capped(messages, None) == messages[2:]
    monkeypatch.setattr(_engine_history, "DEFAULT_CONTEXT_WINDOW", 2000)
    assert capped(messages[2:], None) == messages[4:], "an unknown window is a conservative default, not unbounded"


def test_a_tool_turn_is_dropped_whole() -> None:
    call = ModelResponse([ToolCallPart("read", {"path": "a"}, tool_call_id="c1")])
    result = ModelRequest([ToolReturnPart("read", "text", tool_call_id="c1")])
    messages = [*turn(1), ModelRequest.user_text_prompt("q2"), call, result, ModelResponse([TextPart("a2")])]

    kept = capped(messages, 15)

    assert kept == messages[2:]


def test_a_stopped_turn_drops_unanswered_tool_calls_and_says_so() -> None:
    call = ModelResponse([ToolCallPart("read", {"path": "a"}, tool_call_id="c1")])
    asked = ModelRequest.user_text_prompt("q")

    kept = stopped([asked, call], "so far", "cancelled")
    assert kept[0] == asked
    assert isinstance(kept[1], ModelResponse)
    assert kept[1].parts == [TextPart("so far\n\n[stopped: cancelled]")]
    assert len(kept) == 2
    plain = stopped([asked], "", "cancelled")[-1]
    assert isinstance(plain, ModelResponse)
    assert plain.parts == [TextPart("[stopped: cancelled]")]
    assert stopped([], "x", "cancelled") == []


def test_the_chat_session_is_the_chat_servers_argument() -> None:
    assert session_of([["hz", "system", "serve", "mcp", "--chat", "s-1_a", "--chat-tools", "x"]]) == "s-1_a"
    assert session_of([["hz", "--chat"], ["other"]]) is None
    assert session_of([["hz", "--chat", "../etc"]]) is None
    assert session_of([]) is None


def test_histories_live_in_their_chat_s_folder_and_bad_files_are_a_fresh_start(tmp_path: Path) -> None:
    chats = tmp_path / "chats"
    for session in ("s1", "broken", "old", "linked"):
        (chats / session).mkdir(parents=True)
    store = SessionHistory(chats)
    saved = turn(1)
    store.save("s1", saved, {"answer_start": "a1"})

    loaded, notes = SessionHistory(chats).load("s1")
    assert ModelMessagesTypeAdapter.dump_json(loaded) == ModelMessagesTypeAdapter.dump_json(saved)
    assert notes == {"answer_start": "a1"}
    assert (chats / "s1" / "engine-history.json").stat().st_mode & 0o777 == 0o600
    assert store.load("missing") == ([], {})
    (chats / "broken" / "engine-history.json").write_text("{not json", encoding="utf-8")
    assert store.load("broken") == ([], {})
    (chats / "old" / "engine-history.json").write_text('{"format": 0, "messages": []}', encoding="utf-8")
    assert store.load("old") == ([], {})
    (tmp_path / "elsewhere.json").write_text((chats / "s1" / "engine-history.json").read_text(), encoding="utf-8")
    (chats / "linked" / "engine-history.json").symlink_to(tmp_path / "elsewhere.json")
    assert store.load("linked") == ([], {})
    with pytest.raises(ValueError, match="session id"):
        store.load("../s1")
    with pytest.raises(FileNotFoundError):
        store.save("gone", saved, {})  # a deleted chat: never a folder made for it
    assert not (chats / "gone").exists()
    (chats / "planted").symlink_to(tmp_path)
    with pytest.raises(NotADirectoryError, match="has no folder"):
        store.save("planted", saved, {})  # never through a symlinked chat folder
    store.drop("s1")
    store.drop("s1")
    assert store.load("s1") == ([], {})
