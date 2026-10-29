"""Names are tied to the terminal's foreground process, not another session in the same directory."""

import json

import pytest

from heinzel.heads.web import _agent_titles

pytestmark = pytest.mark.unit


def test_foreground_claude_name_and_rename(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    metadata = sessions / "123.json"
    metadata.write_text(
        json.dumps({"pid": 123, "sessionId": "test-session", "name": "First task", "startedAt": 1_000_000})
    )

    class Process:
        def __init__(self, pid):
            assert pid == 123

        def cmdline(self):
            return ["/usr/local/bin/claude"]

        def environ(self):
            return {"CLAUDE_CONFIG_DIR": str(tmp_path)}

        def create_time(self):
            return 1_000

    monkeypatch.setattr(_agent_titles.os, "tcgetpgrp", lambda _: 123)
    monkeypatch.setattr(_agent_titles.psutil, "Process", Process)
    assert _agent_titles.claude_title(10) == "First task"
    metadata.write_text(
        json.dumps({"pid": 123, "sessionId": "test-session", "name": "Second task", "startedAt": 1_000_000})
    )
    assert _agent_titles.claude_title(10) == "Second task"
    metadata.write_text(
        json.dumps({"pid": 999, "sessionId": "other-session", "name": "Another tab", "startedAt": 1_000_000})
    )
    assert _agent_titles.claude_title(10) is None
    metadata.write_text(json.dumps({"pid": 123, "sessionId": "old-session", "name": "Stale", "startedAt": 100_000}))
    assert _agent_titles.claude_title(10) is None
    metadata.write_text("{")
    assert _agent_titles.claude_title(10) is None


def test_an_unrelated_foreground_process_cannot_expose_a_stale_record(tmp_path, monkeypatch):
    class Process:
        def __init__(self, _pid):
            pass

        def cmdline(self):
            return ["/bin/zsh"]

    monkeypatch.setattr(_agent_titles.os, "tcgetpgrp", lambda _: 123)
    monkeypatch.setattr(_agent_titles.psutil, "Process", Process)
    assert _agent_titles.claude_title(10) is None
