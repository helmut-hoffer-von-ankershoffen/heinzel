"""`hz ai access list|use|test` through the CLI head, with fake `claude` and gateway CLIs on PATH (ADR 0057).

No test reaches a model: the fakes are shell scripts in the test's directory that record how they were called.
"""

import json
import tomllib

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from heinzel import Config, Kernel
from heinzel.heads import api
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
        'case "$1" in auth) echo \'{"loggedIn": true}\';;\n'
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
    assert "installed: claude-code, codex, gateway, pydantic-ai" in result.output


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
