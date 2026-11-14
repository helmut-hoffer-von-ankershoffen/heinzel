"""Chat as an extra place for notices in real Settings, driven by Playwright on an isolated `hz ui serve` (ADR 0160)."""

import json
import tomllib
from pathlib import Path

import pytest

from tests.e2e.web_ui_test import drive_ui_script, environment  # noqa: F401 - pytest fixture

pytestmark = pytest.mark.e2e
REPO = Path(__file__).resolve().parents[2]


@pytest.mark.timeout(600)
def test_notices_also_in_chat_reach_the_owner(environment, tmp_path, monkeypatch):  # noqa: F811
    """Both switches show with the connector's name; notices on, a test notice also reaches the made-up chat."""
    env, _recorded = environment
    metadata = tmp_path / "chat-plugin/heinzel_self_chat_demo-1.0.0.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: heinzel-self-chat-demo\nVersion: 1.0.0\n")
    (metadata / "entry_points.txt").write_text("[heinzel.modules]\nchat-demo = heinzel_self_chat_demo\n")
    inbox = tmp_path / "inbox.jsonl"
    env = env | {
        "PYTHONPATH": ":".join((env["PYTHONPATH"], str(metadata.parent), str(REPO / "tests/fixtures/self_chat"))),
        "HZ_SELF_CHAT_DEMO_INBOX": str(inbox),
        "HZ_SYSTEM_NOTIFY__NATIVE": "false",  # nothing pops up on the machine that runs the test
    }
    monkeypatch.setenv("HZ_SELF_CHAT_DEMO_INBOX", str(inbox))  # the browser script watches it too

    result = drive_ui_script(env, tmp_path, "self-chat.mjs")

    assert result == {"offered": True, "notices": True, "questions": False}
    config = tomllib.loads((Path(env["HZ_CONFIG_HOME"]) / "config.toml").read_text(encoding="utf-8"))
    assert config["system"]["notify"]["chat_notifications"] is True
    sent = [json.loads(line) for line in inbox.read_text(encoding="utf-8").splitlines()]
    assert [entry["context"] for entry in sent] == ["Test"]
    assert sent[0]["text"].startswith("*")
