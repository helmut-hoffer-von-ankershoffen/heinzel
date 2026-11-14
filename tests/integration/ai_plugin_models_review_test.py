"""ADR 0159 Head A review probe R1: the chat the web Conversation and `hz chat send` use, with a plugin's models chosen.

Desired: with ai_access = a plugin's `ai-models` option, `hz chat send` (POST /system/runner/chat/send, the web
Conversation with its ModelPicker) runs on the plugin's model, or refuses clearly; it never hands the plugin's model
id to whatever agent runner happens to be bound.
"""

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from heinzel import Config
from heinzel.heads.cli import build
from tests.integration.chat_test import Script, fake_runner_module

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures/ai_models"))
from heinzel_models_demo import module as demo

pytestmark = pytest.mark.integration


def test_probe_r1_runner_chat_with_plugin_models_selected(make_kernel):
    script, ran = Script(calls=[]), []
    config = Config(files=(), data={"capabilities": {"agent_runner": "fake"}})
    kernel = make_kernel(builtins=True, fakeagent=fake_runner_module(script, ran), models_demo=demo, config=config)
    script.kernel = kernel
    used = CliRunner().invoke(build(kernel), ["ai", "access", "use", "demo-models", "--json"])
    assert used.exit_code == 0, used.output
    # bind the fake runner in the profile's files, as a person whose chat ran on another runner before
    bound = CliRunner().invoke(build(kernel), ["system", "config", "set", "capabilities.agent_runner", "fake"])
    assert bound.exit_code == 0, bound.output
    kernel = make_kernel(
        builtins=True, fakeagent=fake_runner_module(script, ran), models_demo=demo, config=Config.load()
    )
    script.kernel = kernel
    cli = CliRunner()
    session = json.loads(cli.invoke(build(kernel), ["chat", "open", "--json"]).output)["id"]
    sent = cli.invoke(build(kernel), ["chat", "send", session, "hi", "--model", "demo-deep", "--json"])
    assert "brings its own models" in sent.output
    assert script.choices == [], (
        f"the bound agent runner ran with the plugin's model id: {script.choices}; prompts went to it, not the plugin"
    )
