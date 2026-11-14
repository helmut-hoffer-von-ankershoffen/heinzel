"""`hz ai access test --all` in an isolated profile: CLI and web, kept across a restart, seen by the picker (ADR 0159).

The made-up plugin in tests/fixtures/ai_models answers through a `FunctionModel`, so nothing leaves the machine.
"""

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from heinzel import Config, HeinzelError, Kernel
from heinzel.heads.cli import build
from tests.integration.chat_test import AUTH, web_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures/ai_models"))
from heinzel_models_demo import DemoModels
from heinzel_models_demo import module as demo

pytestmark = pytest.mark.integration


def hz(kernel: Kernel, *args: str) -> tuple[int, dict]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    return result.exit_code, json.loads(result.output)


def test_test_all_marks_a_model_that_doesnt_answer_for_every_head_until_it_answers(make_kernel, monkeypatch, tmp_path):
    built = DemoModels.model

    def deep_is_down(self: DemoModels, model_id: str) -> object:
        if model_id == "demo-deep":
            msg = "the proxy lost demo-deep"
            raise HeinzelError(msg)
        return built(self, model_id)

    monkeypatch.setattr(DemoModels, "model", deep_is_down)
    hz(make_kernel(builtins=True, models_demo=demo), "ai", "access", "use", "demo-models")
    code, tested = hz(make_kernel(builtins=True, models_demo=demo), "ai", "access", "test", "--all")
    assert code == 0, tested
    assert [(m["model"], m["outcome"]) for m in tested["models"]] == [
        ("demo-fast", "answered"),
        ("demo-deep", "unanswered"),
        ("demo-retired", "skipped"),
    ]
    assert tested["detail"] == "1 of 2 models answered; 1 marked unavailable until a test answers; 1 skipped"
    assert tested["input_tokens"] > 0
    restarted = make_kernel(builtins=True, models_demo=demo, config=Config.load())
    code, listing = hz(restarted, "ai", "access", "list")
    option = next(o for o in listing["options"] if o["name"] == "demo-models")
    assert option["unavailable_models"] == ["demo-deep", "demo-retired"]
    code, models = hz(make_kernel(builtins=True, models_demo=demo, config=Config.load()), "ai", "models")
    assert [(m["name"], m["available"]) for m in models["models"]] == [
        ("demo-fast", True),
        ("demo-deep", False),
        ("demo-retired", False),
    ]
    code, usage = hz(make_kernel(builtins=True, models_demo=demo), "ai", "usage", "--by", "model")
    assert sorted(row["key"] for row in usage["breakdowns"]["cause"]) == ["test"]
    assert sorted(row["key"] for row in usage["breakdowns"]["model"]) == ["demo-deep", "demo-fast"]

    monkeypatch.setattr(DemoModels, "model", built)  # the proxy has it back
    client = web_client(make_kernel(builtins=True, models_demo=demo, config=Config.load()), tmp_path)
    again = client.post("/api/ai/access/test", headers=AUTH, params={"name": "demo-models", "all": "true"})
    assert again.status_code == 200, again.text
    assert [m["outcome"] for m in again.json()["models"]] == ["answered", "answered", "skipped"]
    listed = client.get("/api/ai/access/list", headers=AUTH).json()
    option = next(o for o in listed["options"] if o["name"] == "demo-models")
    assert option["unavailable_models"] == ["demo-retired"], "a later answer clears it; the plugin's own flag stays"


def test_test_all_says_when_an_option_has_no_models_to_test(make_kernel):
    result = CliRunner().invoke(
        build(make_kernel(builtins=True)), ["ai", "access", "test", "--all", "--name", "gateway"]
    )
    assert result.exit_code == 1
    assert "names no models to test; `hz ai access test --name gateway` checks it." in result.output
