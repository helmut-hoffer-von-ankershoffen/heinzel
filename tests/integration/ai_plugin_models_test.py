"""A plugin's own AI models end to end in an isolated profile: listed, chosen, tested and chatted with (ADR 0159).

The made-up plugin in tests/fixtures/ai_models answers through a `FunctionModel`, so nothing leaves the machine.
"""

import json
import sys
import tomllib
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from heinzel import Config, Kernel, Module, NotSupportedError
from heinzel.heads import api
from heinzel.heads.cli import build
from heinzel.kernel import Document, Documents, user_config_dir
from tests.integration.chat_test import AUTH, web_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures/ai_models"))
from heinzel_models_demo import module as demo

pytestmark = pytest.mark.integration


def hz(kernel: Kernel, *args: str) -> tuple[int, dict]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    return result.exit_code, json.loads(result.output)


def test_the_plugins_models_are_an_ai_access_option_with_their_hosts(make_kernel):
    code, listing = hz(make_kernel(builtins=True, models_demo=demo), "ai", "access", "list")
    assert code == 0
    option = next(o for o in listing["options"] if o["name"] == "demo-models")
    assert (option["title"], option["plugin"], option["module"]) == ("Demo models", "heinzel-test", "models_demo")
    assert option["models"] == ["demo-fast", "demo-deep", "demo-retired"]
    assert option["unavailable_models"] == ["demo-retired"]
    assert (option["model"], option["core_engine"], option["setup"], option["status"]) == (
        "demo-fast",
        True,
        "plugin-model",
        "not tested",
    )
    assert option["hosts"] == ["ai-proxy.example.invalid"]


def test_use_test_and_pick_a_model_through_the_cli(make_kernel):
    code, used = hz(make_kernel(builtins=True, models_demo=demo), "ai", "access", "use", "demo-models")
    assert code == 0, used
    assert used["selected"] == "demo-models"
    assert used["note"] == (
        "Chat, drafts and schedules use Demo models; coding handoffs still run on the agent runner claude-code, "
        "outside these models."
    )
    pinned = tomllib.loads((user_config_dir() / "config.toml").read_text(encoding="utf-8"))
    assert pinned["ai"]["access"]["plugin_model"] == "demo-fast", "pinned: a new first row never changes the model"
    chat = next(use for use in used["uses"] if use["use"] == "chat")
    assert chat["runs"] == "hz's own engine on Demo models's models"
    code, tested = hz(make_kernel(builtins=True, models_demo=demo), "ai", "access", "test", "--name", "demo-models")
    assert (code, tested["ok"], tested["model"], tested["detail"]) == (0, True, "demo-fast", "OK")
    code, _ = hz(
        make_kernel(builtins=True, models_demo=demo),
        "system",
        "config",
        "set",
        "ai.access.plugin_model",
        "demo-retired",
    )
    assert code == 0
    code, refused = hz(make_kernel(builtins=True, models_demo=demo), "ai", "access", "test", "--name", "demo-models")
    assert refused["ok"] is False
    assert "demo-retired is unavailable" in refused["detail"]
    assert refused["fix"] == "hz system config set ai.access.plugin_model demo-fast"
    code, models = hz(make_kernel(builtins=True, models_demo=demo, config=Config.load()), "ai", "models")
    assert [(m["name"], m["available"]) for m in models["models"]] == [
        ("demo-fast", True),
        ("demo-deep", True),
        ("demo-retired", False),
    ]
    assert models["billing"] == "api"


def test_a_chat_turn_through_the_web_head_runs_on_the_plugins_model(make_kernel, tmp_path):
    hz(make_kernel(builtins=True, models_demo=demo), "ai", "access", "use", "demo-models")
    hz(make_kernel(builtins=True, models_demo=demo), "system", "config", "set", "ai.access.plugin_model", "demo-deep")
    kernel = make_kernel(builtins=True, models_demo=demo, config=Config.load())
    client = web_client(kernel, tmp_path)
    created = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "new"})
    assert created.status_code == 200, created.text
    session = created.json()["session_id"]
    accepted = client.post(
        "/api/heinzel/chat", headers=AUTH, json={"session_id": session, "request_id": "hello", "message": "Hello"}
    )
    assert accepted.status_code == 200, accepted.text
    answer = client.post("/api/heinzel/answer-chat", headers=AUTH, json={"session_id": session, "request_id": "hello"})
    assert answer.status_code == 200, answer.text
    message = answer.json()["message"]
    assert message["text"] == "Hello from the demo models"
    assert message["backend_binding"]["backend"] == "demo-models"
    assert message["backend_binding"]["model"] == "demo-deep"
    assert message["billing"] == "api"
    code, usage = hz(make_kernel(builtins=True, models_demo=demo), "ai", "usage", "--by", "access")
    assert code == 0
    assert [row["key"] for row in usage["breakdowns"]["access"]] == ["demo-models"]


class HalfDocs:
    """A documents connector that reads but can't replace a section."""

    unsupported: ClassVar[frozenset[str]] = frozenset({"replace_section"})

    def read(self, ref: str) -> Document:
        return Document(ref=ref, title="Plan", text="x")


def test_not_supported_is_an_answer_on_every_head(make_kernel):
    docs = Module("half-docs", "Reads documents.")
    docs.provides("documents", modes={"read", "write"}, hosts=["docs.invalid"])(HalfDocs)
    user = Module("editor", "Edits a document.")

    @user.operation(read_only=False, requires=["documents:read,write"], heads={"cli", "api"})
    def replace(documents: Documents) -> str:
        """Replace a section."""
        documents.replace_section("plan", "m", "text")
        return "replaced"

    kernel = make_kernel(half_docs=docs, editor=user)
    result = CliRunner().invoke(build(kernel), ["editor", "replace"])
    assert result.exit_code == 1
    assert "This documents connector can't replace section." in result.output
    client = TestClient(api.build(kernel), base_url="http://localhost")
    refused = client.post("/editor/replace")
    assert refused.status_code == 400
    assert refused.json() == {
        "error": "This documents connector can't replace section.",
        "code": "not_supported",
        "capability": "documents",
        "method": "replace_section",
    }
    assert issubclass(NotSupportedError, Exception)
