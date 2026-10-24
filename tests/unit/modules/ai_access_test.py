"""The AI access options and their status words (ADR 0057): gateway, Pydantic AI and Claude Code, without a model."""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from heinzel import AccessStatus, Credentials
from heinzel.kernel import EnvironmentBackend
from heinzel.modules.ai.access._gateway import Finished, GatewayAccess, environment, run_command
from heinzel.modules.ai.access._models import AccessSettings, Bindings, LastTest
from heinzel.modules.ai.access._pydantic import API_KEY, PydanticAiAccess, model_hosts
from heinzel.modules.ai.access._service import ResultStore, state
from heinzel.modules.system.runner._access import ClaudeCodeAccess
from heinzel.modules.system.runner._models import RunnerSettings
from heinzel.modules.system.runner._process import Outcome

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 24, 20, 0, tzinfo=UTC)


def status(**fields: object) -> AccessStatus:
    return AccessStatus.model_validate({"title": "T", "summary": "S", "installed": True} | fields)


@pytest.mark.parametrize(
    ("fields", "last", "word"),
    [
        ({"installed": False}, None, "not installed"),
        ({"signed_in": True}, None, "signed in"),
        ({"signed_in": False}, LastTest(at=NOW, ok=True), "not signed in"),
        ({}, None, "not tested"),
        ({}, LastTest(at=NOW, ok=True), "tested"),
        ({}, LastTest(at=NOW, ok=False), "test failed"),
    ],
)
def test_the_status_word_is_sign_in_first_then_the_last_test(fields, last, word):
    assert state(status(**fields), last) == word


def test_results_are_kept_per_option_and_a_broken_file_reads_as_none(tmp_path):
    store = ResultStore(tmp_path / "ai")
    assert store.load() == {}
    store.keep("alpha", LastTest(at=NOW, ok=True, detail="OK"))
    store.keep("beta", LastTest(at=NOW, ok=False))
    assert {name: last.ok for name, last in store.load().items()} == {"alpha": True, "beta": False}
    assert (tmp_path / "ai").stat().st_mode & 0o077 == 0
    store.path.write_text("{not json", encoding="utf-8")
    assert store.load() == {}


# The gateway


def gateway(run=None, **settings: object) -> GatewayAccess:
    return GatewayAccess(
        AccessSettings.model_validate(settings),
        run=run or (lambda *_: Finished(0, "", "")),
        which=lambda n: f"/bin/{n}",
    )


def test_an_unconfigured_gateway_is_not_installed_and_says_how_to_set_it_up():
    found = gateway().status()
    assert (found.installed, found.signed_in) == (False, None)
    assert "gateway_status_command" in found.fix


def test_a_gateway_cli_missing_from_path_is_not_installed():
    option = GatewayAccess(AccessSettings(gateway_status_command=["acme", "status"]), which=lambda _: None)
    assert "acme is not on PATH" in option.status().detail


def test_the_gateway_status_reads_json_and_never_spends(tmp_path):
    seen: list[list[str]] = []

    def run(command, env, timeout):
        seen.append(command)
        assert "HZ_CREDENTIAL_X_Y" not in env
        return Finished(0, '{"signed_in": true, "models": ["acme-large"], "detail": "jo@acme.example"}', "")

    found = gateway(run, gateway_status_command=["acme", "ai", "status"], gateway_runner="command").status()
    assert seen == [["/bin/acme", "ai", "status"]]
    assert (found.installed, found.signed_in, found.models, found.runner) == (True, True, ["acme-large"], "command")
    assert found.detail == "jo@acme.example"


def test_a_failing_status_command_means_not_signed_in_with_the_sign_in_hint():
    run = lambda *_: Finished(1, "", "token expired")  # noqa: E731
    found = gateway(run, gateway_status_command=["acme", "status"], gateway_sign_in="acme login").status()
    assert (found.signed_in, found.detail, found.fix) == (False, "token expired", "acme login")


def test_a_status_command_past_its_timeout_tells_nothing():
    found = gateway(lambda *_: Finished(None, "", ""), gateway_status_command=["acme"]).status()
    assert (found.installed, found.signed_in) == (True, None)


def test_the_gateway_test_runs_its_command_and_reads_json():
    run = lambda *_: Finished(0, '{"ok": true, "detail": "pong", "model": "acme-small", "cost_usd": 0.001}', "")  # noqa: E731
    result = gateway(run, gateway_test_command=["acme", "ping"]).test()
    assert (result.ok, result.detail, result.model, result.cost_usd) == (True, "pong", "acme-small", 0.001)


@pytest.mark.parametrize(
    ("finished", "ok", "detail"),
    [
        (Finished(0, "pong\n", ""), True, "pong"),
        (Finished(2, "", "denied"), False, "denied"),
        (Finished(None, "", ""), False, "stopped after 90s"),
    ],
)
def test_the_gateway_test_without_json(finished, ok, detail):
    result = gateway(lambda *_: finished, gateway_test_command=["acme", "ping"]).test()
    assert (result.ok, result.detail) == (ok, detail)


def test_a_gateway_without_a_test_command_fails_its_test():
    assert not gateway().test().ok


def test_the_environment_is_an_allowlist(monkeypatch):
    monkeypatch.setenv("ACME_TOKEN", "t")
    monkeypatch.setenv("SECRET", "s")
    monkeypatch.setenv("HZ_CREDENTIAL_AI_KEY", "k")
    env = environment(["ACME_TOKEN", "HZ_CREDENTIAL_AI_KEY"])
    assert env["ACME_TOKEN"] == "t"
    assert "SECRET" not in env
    assert "HZ_CREDENTIAL_AI_KEY" not in env


def test_a_real_command_runs_without_a_shell_in_an_empty_directory_with_a_timeout(tmp_path):
    script = tmp_path / "gw.py"
    script.write_text("import os, sys\nprint(os.listdir('.'))\nprint('x' * 100000)\n", encoding="utf-8")
    finished = run_command([sys.executable, str(script)], dict(os.environ), 30)
    assert finished.returncode == 0
    assert finished.stdout.startswith("[]")
    assert len(finished.stdout.encode()) <= 64 * 1024
    slow = tmp_path / "slow.py"
    slow.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    assert run_command([sys.executable, str(slow)], dict(os.environ), 0.5).returncode is None


# Pydantic AI


def pydantic(model: str | None, credentials: Credentials | None = None, build=None, installed=True):
    return PydanticAiAccess(
        AccessSettings(model=model),
        credentials or Credentials(EnvironmentBackend(), [("ai.access", API_KEY)]),
        build=build or (lambda _model, _key: TestModel(custom_output_text="OK")),
        installed=lambda _package: installed,
    )


def test_pydantic_ai_without_a_model_says_how_to_set_one():
    found = pydantic(None).status()
    assert (found.installed, "ai.access.model" in found.fix) == (False, True)


def test_pydantic_ai_names_the_extra_when_the_vendor_client_is_missing():
    found = pydantic("anthropic:claude-sonnet-4-5", installed=False).status()
    assert found.fix == "uv tool install --reinstall 'heinzel[anthropic]'"


def test_pydantic_ai_is_signed_in_with_the_vendor_variable_or_the_credential(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert pydantic("anthropic:claude-sonnet-4-5").status().signed_in is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert "ANTHROPIC_API_KEY" in pydantic("anthropic:claude-sonnet-4-5").status().detail
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-stored")
    found = pydantic("anthropic:claude-sonnet-4-5").status()
    assert (found.signed_in, "credential" in found.detail) == (True, True)


def test_a_pydantic_ai_test_is_one_turn_through_the_model(monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-stored")
    keys: list[str | None] = []

    def build(model: str, key: str | None) -> TestModel:
        keys.append(key)
        return TestModel(custom_output_text="OK")

    result = pydantic("anthropic:claude-sonnet-4-5", build=build).test()
    assert (result.ok, result.detail, result.model) == (True, "OK", "anthropic:claude-sonnet-4-5")
    assert keys == ["sk-stored"]


def test_a_failing_model_is_a_failed_test_without_the_key(monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-secret")

    def refuse(messages, info: AgentInfo):
        msg = "invalid key sk-secret"
        raise RuntimeError(msg)

    result = pydantic("openai:gpt-5", build=lambda _m, _k: FunctionModel(refuse)).test()
    assert not result.ok
    assert "sk-secret" not in result.detail
    assert "RuntimeError" in result.detail


def test_the_model_hosts_follow_the_vendor():
    assert model_hosts(AccessSettings(model="openai:gpt-5")) == ["api.openai.com"]
    assert model_hosts(AccessSettings()) == []


# Claude Code


def claude(tmp_path: Path, logged_in: str, answer: str = "OK") -> ClaudeCodeAccess:
    def execute(command, **_):
        if command[1:3] == ["auth", "status"]:
            return Outcome(0, f'{{"loggedIn": {logged_in}}}', "")
        result = f'{{"type": "result", "subtype": "success", "result": "{answer}", "total_cost_usd": 0.002}}'
        return Outcome(0, result + "\n", "")

    return ClaudeCodeAccess(RunnerSettings(), execute=execute, which=lambda _: str(tmp_path / "claude"))


def test_claude_code_is_signed_in_from_auth_status_and_names_its_runner(tmp_path):
    found = claude(tmp_path, "true").status()
    assert (found.signed_in, found.runner) == (True, "claude-code")
    assert claude(tmp_path, "false").status().signed_in is False


def test_claude_code_missing_is_not_installed():
    found = ClaudeCodeAccess(RunnerSettings(), which=lambda _: None).status()
    assert (found.installed, found.fix.startswith("Install Claude Code")) == (False, True)
    assert not ClaudeCodeAccess(RunnerSettings(), which=lambda _: None).test().ok


def test_a_claude_code_test_is_one_short_answer_with_its_cost(tmp_path):
    result = claude(tmp_path, "true").test()
    assert (result.ok, result.detail, result.cost_usd) == (True, "OK", 0.002)


def test_a_project_file_can_set_nothing_that_decides_what_runs():
    assert set(AccessSettings.model_fields) <= AccessSettings.ignored_in_project
    assert {"ai_access", "agent_runner"} <= Bindings.ignored_in_project


def test_a_vendor_variable_key_is_kept_out_of_a_failure_too(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")

    def refuse(messages, info: AgentInfo):
        msg = "invalid key sk-from-env"
        raise RuntimeError(msg)

    result = pydantic("openai:gpt-5", build=lambda _m, _k: FunctionModel(refuse)).test()
    assert "sk-from-env" not in result.detail
