"""The AI access options and their status words (ADR 0057): gateway, Pydantic AI and Claude Code, without a model."""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from heinzel import AccessStatus, Config, Credentials, Settings
from heinzel.kernel import EnvironmentBackend
from heinzel.modules.ai.access._discovery import ModelCache, fetch, is_local, listing
from heinzel.modules.ai.access._gateway import Finished, GatewayAccess, environment, run_command
from heinzel.modules.ai.access._models import AccessSettings, Bindings, LastTest
from heinzel.modules.ai.access._pydantic import API_KEY, PydanticAiAccess, endpoint_model, model_hosts
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


def offline(url: str, headers: dict[str, str]) -> bytes:
    msg = "offline"
    raise OSError(msg)


def pydantic(  # noqa: PLR0913 - every seam of the option
    model: str | None,
    *,
    credentials: Credentials | None = None,
    build=None,
    installed=True,
    base_url: str | None = None,
    get=offline,
):
    return PydanticAiAccess(
        AccessSettings(model=model, base_url=base_url),
        credentials or Credentials(EnvironmentBackend(), [("ai.access", API_KEY)]),
        Config.empty().settings(Settings, "hz"),
        build=build or (lambda _model, _key, _base: TestModel(custom_output_text="OK")),
        installed=lambda _package: installed,
        get=get,
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

    def build(model: str, key: str | None, base: str | None) -> TestModel:
        keys.append(key)
        return TestModel(custom_output_text="OK")

    result = pydantic("anthropic:claude-sonnet-4-5", build=build).test()
    assert (result.ok, result.model) == (True, "anthropic:claude-sonnet-4-5")
    assert result.detail.startswith("OK; models not listed")  # offline here: the listing is a note, not a failure
    assert keys == ["sk-stored"]


def test_a_failing_model_is_a_failed_test_without_the_key(monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-secret")

    def refuse(messages, info: AgentInfo):
        msg = "invalid key sk-secret"
        raise RuntimeError(msg)

    result = pydantic("openai:gpt-5", build=lambda _m, _k, _b: FunctionModel(refuse)).test()
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

    result = pydantic("openai:gpt-5", build=lambda _m, _k, _b: FunctionModel(refuse)).test()
    assert "sk-from-env" not in result.detail


# Model APIs: OpenAI-compatible endpoints and model discovery


def test_an_endpoint_needs_no_key_and_its_listing_feeds_the_models(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    asked: list[tuple[str, dict[str, str]]] = []

    def get(url: str, headers: dict[str, str]) -> bytes:
        asked.append((url, headers))
        return b'{"data": [{"id": "llama3.1:8b"}, {"id": "qwen3:14b"}, {"id": "--bad"}, {"x": 1}]}'

    built: list[tuple[str, str | None, str | None]] = []

    def build(model: str, key: str | None, base: str | None) -> TestModel:
        built.append((model, key, base))
        return TestModel(custom_output_text="OK")

    option = pydantic("llama3.1:8b", base_url="http://localhost:11434/v1/", build=build, get=get)
    before = option.status()
    assert (before.installed, before.signed_in, before.models) == (True, None, ["llama3.1:8b"])
    assert "no key" in before.detail
    result = option.test()
    assert result.ok
    assert "2 models listed" in result.detail
    assert asked == [("http://localhost:11434/v1/models", {"Accept": "application/json"})]  # no key sent
    assert built == [("llama3.1:8b", None, "http://localhost:11434/v1/")]
    assert option.status().models == ["llama3.1:8b", "qwen3:14b"]  # kept for the picker, offline


def test_a_key_goes_only_over_https_or_to_this_machine(monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-endpoint")
    sent: list[dict[str, str]] = []

    def get(url: str, headers: dict[str, str]) -> bytes:
        sent.append(headers)
        return b'{"data": []}'

    remote = pydantic("some-model", base_url="http://models.example.net/v1", get=get)
    assert remote.status().signed_in is False
    assert not remote.test().ok
    assert sent == []  # the key never left
    secure = pydantic("some-model", base_url="https://openrouter.ai/api/v1", get=get)
    assert secure.test().ok
    assert sent == [{"Accept": "application/json", "Authorization": "Bearer sk-endpoint"}]


def test_anthropic_and_openai_list_their_models_with_their_own_headers(monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-vendor")
    seen: list[tuple[str, dict[str, str]]] = []

    def get(url: str, headers: dict[str, str]) -> bytes:
        seen.append((url, headers))
        return b'{"data": [{"id": "claude-sonnet-4-5"}]}'

    assert pydantic("anthropic:claude-sonnet-4-5", get=get).test().ok
    assert pydantic("openai:gpt-5", get=get).test().ok
    assert seen[0] == (
        "https://api.anthropic.com/v1/models",
        {"Accept": "application/json", "x-api-key": "sk-vendor", "anthropic-version": "2023-06-01"},
    )
    assert seen[1] == (
        "https://api.openai.com/v1/models",
        {"Accept": "application/json", "Authorization": "Bearer sk-vendor"},
    )


def test_a_failed_listing_is_a_note_without_the_key_and_the_test_goes_on(monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-secret")

    def get(url: str, headers: dict[str, str]) -> bytes:
        msg = "refused sk-secret"
        raise OSError(msg)

    result = pydantic("openai:gpt-5", get=get).test()
    assert result.ok
    assert "models not listed" in result.detail
    assert "sk-secret" not in result.detail


def test_a_model_without_a_vendor_or_an_endpoint_says_what_to_set():
    found = pydantic("llama3").status()
    assert not found.installed
    assert "base_url" in found.fix


def test_the_endpoint_s_host_is_what_the_matrix_shows():
    assert model_hosts(AccessSettings(model="m", base_url="http://localhost:1234/v1")) == ["localhost"]


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("http://localhost:11434/v1", True),
        ("http://127.0.0.1:8000", True),
        ("http://[::1]:8000", True),
        ("http://models.example.net", False),
        ("https://openrouter.ai/api/v1", False),
        ("http://127.0.0.1.example.net", False),
        ("http://localhost.example.net", False),
        ("http://[::ffff:127.0.0.1]:80", True),
        ("http://[::ffff:192.0.2.1]:80", False),
    ],
)
def test_what_counts_as_this_machine(url, local):
    assert is_local(url) is local


def test_the_listing_refuses_what_isn_t_one():
    with pytest.raises(ValueError, match="no `data` list"):
        listing("openai", None, None, lambda _u, _h: b'{"models": []}')
    with pytest.raises(ValueError, match="isn't JSON"):
        listing("openai", None, None, lambda _u, _h: b"<html>")
    assert listing("google-gla", None, None, offline) == []  # no listing endpoint known: nothing asked


def test_fetch_refuses_other_schemes():
    with pytest.raises(ValueError, match="http or https"):
        fetch("file:///etc/passwd", {})


@pytest.mark.parametrize(
    "url", ["https://user:secret@models.example.net/v1", "https://models.example.net/v1?key=secret", "http://x/#k"]
)
def test_a_base_url_can_hide_no_credentials(url):
    with pytest.raises(ValueError, match="base_url"):
        AccessSettings(base_url=url)


def test_an_endpoint_s_name_with_a_colon_without_base_url_asks_for_the_endpoint():
    found = pydantic("llama3.1:8b").status()
    assert not found.installed
    assert "base_url" in found.fix


def test_a_vendor_prefix_left_on_an_endpoint_s_model_is_dropped():
    assert endpoint_model("openai:gpt-5") == "gpt-5"
    assert endpoint_model("llama3.1:8b") == "llama3.1:8b"


def test_an_endpoint_s_key_from_the_keychain_goes_with_the_listing_and_the_turn(monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-local")
    sent: list[dict[str, str]] = []
    built: list[str | None] = []

    def get(url: str, headers: dict[str, str]) -> bytes:
        sent.append(headers)
        return b'{"data": [{"id": "tiny"}]}'

    def build(model: str, key: str | None, base: str | None) -> TestModel:
        built.append(key)
        return TestModel(custom_output_text="OK")

    assert pydantic("tiny", base_url="http://127.0.0.1:8000/v1", get=get, build=build).test().ok
    assert sent == [{"Accept": "application/json", "Authorization": "Bearer sk-local"}]
    assert built == ["sk-local"]


def test_the_cache_keeps_each_source_and_survives_a_broken_file(tmp_path):
    cache = ModelCache(tmp_path / "ai")
    assert cache.load("openai") == []
    cache.keep("openai", ["gpt-5"])
    cache.keep("http://localhost:11434/v1", ["llama3.1:8b", "--bad"])
    assert cache.load("openai") == ["gpt-5"]
    assert cache.load("http://localhost:11434/v1") == ["llama3.1:8b"]  # an id that isn't one is left out
    cache.path.write_text("[not json", encoding="utf-8")
    assert cache.load("openai") == []
    cache.keep("openai", ["gpt-5"])
    assert cache.load("openai") == ["gpt-5"]
    assert (tmp_path / "ai").stat().st_mode & 0o077 == 0


def test_a_listing_too_large_is_refused_not_cut(monkeypatch):
    import heinzel.modules.ai.access._discovery as discovery

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, size):
            return b"x" * size

    class Opener:
        def open(self, request, timeout):
            return Response()

    monkeypatch.setattr(discovery, "LOCAL_OPENER", Opener())
    with pytest.raises(ValueError, match="larger than"):
        discovery.fetch("http://localhost:1/v1/models", {})
