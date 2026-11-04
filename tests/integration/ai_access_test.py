"""`hz ai access list|use|test` through the CLI head, with fake `claude` and gateway CLIs on PATH (ADR 0057).

No test reaches a model: the fakes are shell scripts in the test's directory that record how they were called.
"""

import importlib.util
import json
import tomllib

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from heinzel import Config, Kernel
from heinzel.heads import api, mcp
from heinzel.heads._exposure import exposed
from heinzel.heads.cli import build
from heinzel.kernel import user_config_dir

pytestmark = pytest.mark.integration


def hz(kernel: Kernel, *args: str) -> tuple[int, dict]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    assert result.output.startswith("{"), result.output
    return result.exit_code, json.loads(result.output)


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    """A `claude` that is logged in and answers OK, and an `acme` gateway CLI; both record their arguments."""
    calls = tmp_path / "calls"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text(
        f'#!/bin/sh\necho "claude $*" >> {calls}\n'
        'case "$1" in auth) echo \'{"loggedIn": true, "authMethod": "claude.ai"}\';;\n'
        '*) echo \'{"type": "result", "subtype": "success", "result": "OK", "total_cost_usd": 0.001}\';; esac\n',
        encoding="utf-8",
    )
    acme = bin_dir / "acme"
    acme.write_text(
        f'#!/bin/sh\necho "acme $*" >> {calls}\n'
        'case "$2" in status) echo \'{"signed_in": true, "models": ["acme-large"]}\';;\n'
        "ping) echo pong;; esac\n",
        encoding="utf-8",
    )
    claude.chmod(0o755)
    acme.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    return calls


def gateway_config() -> Config:
    access = {
        "gateway_title": "Acme AI",
        "gateway_status_command": ["acme", "ai", "status"],
        "gateway_test_command": ["acme", "ai", "ping"],
        "gateway_runner": "command",
        "gateway_hosts": ["https://ai.acme.example/v1"],
    }
    return Config(files=(), data={"ai": {"access": access}})


def user_file() -> dict:
    return tomllib.loads((user_config_dir() / "config.toml").read_text(encoding="utf-8"))


def test_the_listing_shows_every_option_with_the_one_in_use(make_kernel, fakes):
    code, listing = hz(make_kernel(builtins=True, config=gateway_config()), "ai", "access", "list")
    assert code == 0
    assert listing["selected"] == "claude-code"
    # codex reads this machine's own `codex`, if any: its status is the machine's, so only its presence is checked
    rows = {o["name"]: (o["in_use"], o["status"], o["hosts"]) for o in listing["options"] if o["name"] != "codex"}
    assert "codex" in {o["name"] for o in listing["options"]}
    assert rows == {
        "claude-code": (True, "signed in", ["api.anthropic.com"]),
        "gateway": (False, "signed in", ["ai.acme.example"]),
        "pydantic-ai": (False, "not installed", []),
        "chatgpt": (False, "not signed in", ["api.openai.com", "auth.openai.com"]),
    }
    assert [u["use"] for u in listing["uses"]] == ["chat", "turns", "coding"]
    assert set(fakes.read_text(encoding="utf-8").splitlines()) == {"claude auth status --json", "acme ai status"}


def test_use_binds_the_access_and_the_runner_it_names(make_kernel, fakes):
    kernel = make_kernel(builtins=True, config=gateway_config())
    code, listing = hz(kernel, "ai", "access", "use", "gateway")
    assert code == 0, listing
    assert user_file()["capabilities"] == {"ai_access": "gateway", "agent_runner": "command"}
    assert listing["selected"] == "gateway"
    code, listing = hz(kernel, "ai", "access", "use", "claude-code")
    assert user_file()["capabilities"] == {"ai_access": "claude-code", "agent_runner": "claude-code"}


def test_use_refuses_an_option_nobody_installed(make_kernel, fakes):
    result = CliRunner().invoke(build(make_kernel(builtins=True)), ["ai", "access", "use", "nope"])
    assert result.exit_code != 0
    assert "installed: chatgpt, claude-code, codex, gateway, pydantic-ai" in result.output


def test_a_test_is_explicit_kept_and_shown_in_the_listing(make_kernel, fakes):
    kernel = make_kernel(builtins=True, config=gateway_config())
    code, report = hz(kernel, "ai", "access", "test", "--name", "gateway")
    assert (code, report["ok"], report["detail"]) == (0, True, "pong")
    code, report = hz(kernel, "ai", "access", "test")  # the one in use
    assert (report["name"], report["ok"], report["cost_usd"]) == ("claude-code", True, 0.001)
    claude_run = [line for line in fakes.read_text(encoding="utf-8").splitlines() if " -p " in line]
    assert len(claude_run) == 1
    assert "--max-budget-usd 0.05" in claude_run[0]
    listing = hz(kernel, "ai", "access", "list")[1]
    assert {o["name"]: o["last_test"]["ok"] for o in listing["options"] if o["last_test"]} == {
        "gateway": True,
        "claude-code": True,
    }


def test_doctor_reports_a_runner_that_does_not_follow_the_access(make_kernel, fakes):
    kernel = make_kernel(builtins=True, config=gateway_config())
    hz(kernel, "ai", "access", "use", "gateway")
    (user_config_dir() / "config.toml").write_text('[capabilities]\nai_access = "gateway"\n', encoding="utf-8")
    _, report = hz(kernel, "system", "doctor")
    [check] = report["checks"]["ai.access"]
    assert check["status"] == "degraded"
    assert check["fix"] == "hz ai access use gateway"


def test_the_rest_head_has_no_ai_access(make_kernel):
    client = TestClient(api.build(make_kernel(builtins=True)), base_url="http://localhost")
    assert client.get("/ai/access/list").status_code == 404
    assert client.post("/ai/access/use", json={"name": "gateway"}).status_code in {404, 405}
    assert client.get("/ai/usage").status_code == 404


def test_every_run_is_recorded_and_hz_ai_usage_reports_it(make_kernel, fakes):
    kernel = make_kernel(builtins=True, config=gateway_config())
    hz(kernel, "ai", "access", "test")  # Claude Code: one short answer, $0.001
    code, answer = hz(kernel, "system", "runner", "ask", "What is hz?")
    assert (code, answer["status"]) == (0, "completed")
    code, usage = hz(kernel, "ai", "usage", "--by", "cause")
    assert code == 0
    assert usage["today"]["runs"] == 2
    assert usage["today"]["cost_usd"] == pytest.approx(0.002)
    assert {row["key"] for row in usage["breakdowns"]["cause"]} == {"test", "ask"}
    assert [row["key"] for row in usage["breakdowns"]["access"]] == ["claude-code"]
    assert usage["tone"] == "ok"


def test_hz_ai_models_lists_the_models_and_efforts_of_the_access_in_use(make_kernel, fakes):
    code, listed = hz(make_kernel(builtins=True), "ai", "models")
    assert code == 0
    assert (listed["access"], listed["title"], listed["billing"]) == ("claude-code", "Claude Code", "plan")
    assert [model["name"] for model in listed["models"]][:2] == ["fable", "opus"]
    efforts = {effort["name"]: effort for effort in listed["efforts"]}
    assert list(efforts) == ["low", "medium", "high", "xhigh", "max"]
    assert efforts["medium"]["recommended"] is True
    assert efforts["xhigh"]["title"] == "Extra"
    assert efforts["max"]["warning"] == "≈3.5x or more usage"
    assert set(fakes.read_text(encoding="utf-8").splitlines()) == {"claude auth status --json"}  # nothing spent


def test_the_rest_head_has_no_models(make_kernel):
    client = TestClient(api.build(make_kernel(builtins=True)), base_url="http://localhost")
    assert client.get("/ai/models").status_code == 404


def test_signing_in_to_chatgpt_is_a_terminal_act_only(make_kernel):
    kernel = make_kernel(builtins=True)
    names = {item.name for item in exposed(kernel, mcp.HEAD)}
    assert "ai_access_list" in names
    assert "ai_access_login" not in names
    client = TestClient(api.build(kernel), base_url="http://localhost")
    assert client.post("/ai/access/login").status_code in {404, 405}


@pytest.mark.parametrize("selected", ["claude-code", "chatgpt", "pydantic-ai"])
def test_doctor_only_requires_authentication_for_the_selected_access(make_kernel, fakes, selected):
    user_config_dir().mkdir(parents=True, exist_ok=True)
    (user_config_dir() / "config.toml").write_text(
        f'[capabilities]\nai_access = "{selected}"\nagent_runner = "claude-code"\n', encoding="utf-8"
    )
    kernel = make_kernel(builtins=True, config=Config.load())
    _, report = hz(kernel, "system", "doctor")
    credentials = report["checks"]["system.credential"]
    assert all(check["status"] == "up" for check in credentials)
    assert all("not stored" in check["detail"] for check in credentials if check["name"].startswith("credential "))
    [access] = report["checks"]["ai.access"]
    assert access["status"] == ("up" if selected == "claude-code" else "degraded")


def test_selected_configured_vendor_still_degrades_when_its_api_key_is_missing(make_kernel, fakes, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", raising=False)
    user_config_dir().mkdir(parents=True, exist_ok=True)
    (user_config_dir() / "config.toml").write_text(
        '[capabilities]\nai_access = "pydantic-ai"\nagent_runner = "claude-code"\n'
        '[ai.access]\nmodel = "anthropic:claude-sonnet-4-5"\n',
        encoding="utf-8",
    )
    kernel = make_kernel(builtins=True, config=Config.load())
    # Only SDK presence is simulated; selected-provider status and doctor execute normally without network.
    find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda package: object() if package == "anthropic" else find_spec(package)
    )
    _, listing = hz(kernel, "ai", "access", "list")
    chosen = next(option for option in listing["options"] if option["in_use"])
    assert chosen["status"] == "not signed in"
    assert chosen["detail"] == "no API key for anthropic"
    _, report = hz(kernel, "system", "doctor")
    assert all(check["status"] == "up" for check in report["checks"]["system.credential"])
    [access] = report["checks"]["ai.access"]
    assert access["status"] == "degraded"
    assert "no API key for anthropic" in access["detail"]


def test_explicitly_deferred_ai_is_honest_and_ask_chat_fail_before_cli_or_network(make_kernel, fakes):
    user_config_dir().mkdir(parents=True, exist_ok=True)
    (user_config_dir() / "config.toml").write_text('[capabilities]\nai_access="none"\nagent_runner="none"\n')
    kernel = make_kernel(builtins=True, config=Config.load())
    _, report = hz(kernel, "system", "doctor")
    assert report["checks"]["ai.access"][0]["status"] == "up"
    assert "intentionally not configured" in report["checks"]["ai.access"][0]["detail"]
    relevant = [c for c in report["checks"]["system.capability"] if "agent-runner" in c["name"]]
    assert len(relevant) == 2
    assert all(c["status"] == "up" and "on-demand" in c["detail"] for c in relevant)
    assert not fakes.exists(), "explicit deferral must not even probe provider authentication"
    for arguments in [
        ["system", "runner", "ask", "Hello"],
        ["chat", "send", "0123456789abcdef", "Hello"],
    ]:
        result = CliRunner().invoke(build(kernel), arguments)
        assert result.exit_code != 0
        assert "no adapter bound" in result.output
    assert not fakes.exists()
