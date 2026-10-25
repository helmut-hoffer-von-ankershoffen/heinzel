"""The `chatgpt` AI access option: Sign in with ChatGPT through Pydantic AI, kept in the keychain (ADR 0057).

No test reaches OpenAI: the browser, the callback and the model are replaced; the sign-in flow itself is Pydantic AI's.
"""

import asyncio
import json
from collections.abc import Coroutine
from contextlib import contextmanager
from typing import Any

import pytest
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTCredentials

from heinzel import Config, Credentials, HeinzelError, Progress, Settings
from heinzel.kernel import EnvironmentBackend
from heinzel.modules.ai.access._chatgpt import (
    CHATGPT_LOGIN,
    ChatGptAccess,
    KeychainCredentialSource,
    _without_hint,
    host_id,
    run_coroutine,
)
from heinzel.modules.ai.access._models import AccessSettings
from heinzel.testing import MemoryBackend

pytestmark = pytest.mark.unit

PLAN = "openid offline_access chatgpt.tokens.use.direct"
SIGNED = OpenAIChatGPTCredentials(
    client_id="client-1", access_token="at", refresh_token="rt", id_token=None, scope=PLAN
)


class Recorder(Progress):
    def __init__(self) -> None:
        self.tasks: list[str] = []

    @contextmanager
    def task(self, description: str, total: float | None = None):  # type: ignore[override]
        self.tasks.append(description)
        yield lambda _amount: None


def keychain() -> Credentials:
    return Credentials(MemoryBackend(), [("ai.access", CHATGPT_LOGIN)])


def option(credentials: Credentials | None = None, model: str | None = "gpt-test", **seams: Any) -> ChatGptAccess:
    return ChatGptAccess(
        AccessSettings(chatgpt_model=model),
        credentials or keychain(),
        Config.empty().settings(Settings, "hz"),
        **({"installed": lambda _package: True} | seams),
    )


def signed_in(scope: str = PLAN) -> Credentials:
    credentials = keychain()
    stored = {"client_id": "client-1", "access_token": "at", "refresh_token": "rt", "id_token": None, "scope": scope}
    credentials.set(CHATGPT_LOGIN, json.dumps(stored))
    return credentials


def test_status_says_what_is_missing_without_calling_anyone():
    assert option(installed=lambda _p: False).status().fix == "uv tool install --reinstall 'heinzel[openai]'"
    assert (option().status().signed_in, option().status().fix) == (False, "hz ai access login")
    declined = option(signed_in("openid offline_access")).status()
    assert (declined.signed_in, "plan usage" in declined.detail) == (False, True)
    unset = option(signed_in(), model=None).status()
    assert (unset.signed_in, "chatgpt_model" in unset.fix) == (True, True)
    ready = option(signed_in()).status()
    assert (ready.signed_in, ready.models) == (True, ["gpt-test"])


def test_a_broken_stored_sign_in_reads_as_none():
    credentials = keychain()
    credentials.set(CHATGPT_LOGIN, "{not json")
    assert option(credentials).status().signed_in is False
    credentials.set(CHATGPT_LOGIN, json.dumps({"client_id": 1}))
    assert option(credentials).status().signed_in is False


def test_the_keychain_source_round_trips_pydantic_ai_s_credentials():
    credentials = keychain()
    source = KeychainCredentialSource(credentials)
    with pytest.raises(HeinzelError, match="hz ai access login"):
        asyncio.run(source.load())
    asyncio.run(source.save(SIGNED))
    assert asyncio.run(source.load()) == SIGNED
    assert "rt" not in repr(asyncio.run(source.load()))  # the tokens stay out of reprs


def closing(result: object):
    """A run that never starts the coroutine it gets (no browser, no callback) and returns `result`."""

    def run(coroutine: Coroutine[Any, Any, Any]) -> object:
        coroutine.close()
        if isinstance(result, BaseException):
            raise result
        return result

    return run


def test_login_opens_the_browser_and_keeps_the_sign_in_in_the_keychain():
    credentials, progress, opened = keychain(), Recorder(), []
    access = option(credentials, run=closing(SIGNED))
    status = access.login(progress, open_browser=lambda url: opened.append(url) or True)
    assert opened[0].startswith("https://auth.openai.com/")
    assert progress.tasks == ["Sign in to ChatGPT in the browser that opened; waiting up to 5 minutes"]
    assert status.signed_in is True
    assert json.loads(credentials.get(CHATGPT_LOGIN) or "")["refresh_token"] == "rt"


def test_login_shows_the_url_only_when_no_browser_opened():
    progress = Recorder()
    option(run=closing(SIGNED)).login(progress, open_browser=lambda _url: False)
    assert progress.tasks[0].startswith("Sign in to ChatGPT at https://auth.openai.com/")


def test_a_declined_or_late_sign_in_is_an_error_and_keeps_nothing():
    credentials = keychain()
    with pytest.raises(HeinzelError, match="didn't finish: TimeoutError"):
        option(credentials, run=closing(TimeoutError())).login(Recorder(), open_browser=lambda _url: True)
    assert credentials.get(CHATGPT_LOGIN) is None


def test_login_without_openai_s_client_says_how_to_get_it():
    with pytest.raises(HeinzelError, match=r"heinzel\[openai\]"):
        option(installed=lambda _p: False).login(Recorder())


def test_a_test_is_one_turn_on_the_plan_through_the_keychain():
    seen: list[object] = []

    def build(name: str, source: KeychainCredentialSource) -> TestModel:
        seen.append((name, asyncio.run(source.load()).client_id))
        return TestModel(custom_output_text="OK")

    result = option(signed_in(), build=build).test()
    assert (result.ok, result.detail, result.model) == (True, "OK", "gpt-test")
    assert seen == [("gpt-test", "client-1")]
    assert not option().test().ok  # not signed in: nothing runs


def test_the_installation_s_id_is_made_once_and_kept(tmp_path):
    first = host_id(tmp_path / "ai")
    assert first.startswith("urn:uuid:")
    assert host_id(tmp_path / "ai") == first


def environment() -> Credentials:
    return Credentials(EnvironmentBackend(), [("ai.access", CHATGPT_LOGIN)])


def test_without_a_keychain_login_is_refused_before_the_browser_opens():
    opened: list[str] = []
    with pytest.raises(HeinzelError, match="needs a keychain"):
        option(environment(), run=closing(SIGNED)).login(
            Recorder(), open_browser=lambda url: opened.append(url) or True
        )
    assert opened == []


def test_without_a_keychain_a_refresh_lives_in_memory_and_status_says_so(monkeypatch):
    stored = {"client_id": "c", "access_token": "at", "refresh_token": "rt", "id_token": None, "scope": PLAN}
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_CHATGPT_LOGIN", json.dumps(stored))
    credentials = environment()
    asyncio.run(KeychainCredentialSource(credentials).save(SIGNED))  # nothing raised, nothing kept
    assert "this process only" in option(credentials).status().detail


def test_a_sign_in_without_plan_usage_says_to_allow_it():
    declined = OpenAIChatGPTCredentials(client_id="c", access_token="at", refresh_token="rt", scope="openid")
    status = option(run=closing(declined)).login(Recorder(), open_browser=lambda _url: True)
    assert (status.signed_in, "allow hz" in status.detail) == (False, True)


def test_a_sign_in_that_can_t_be_kept_says_so():
    class Broken(MemoryBackend):
        def set(self, service: str, name: str, value: str) -> None:
            msg = "the keychain is locked"
            raise HeinzelError(msg)

    credentials = Credentials(Broken(), [("ai.access", CHATGPT_LOGIN)])
    with pytest.raises(HeinzelError, match="Signed in to ChatGPT, but keeping the sign-in failed"):
        option(credentials, run=closing(SIGNED)).login(Recorder(), open_browser=lambda _url: True)


def test_the_shown_url_leaves_out_the_earlier_id_token():
    url = "https://auth.openai.com/authorize?client_id=c&id_token_hint=secret.jwt&state=s"
    assert _without_hint(url) == "https://auth.openai.com/authorize?client_id=c&state=s"


def test_a_coroutine_runs_here_or_on_its_own_thread_when_a_loop_already_runs():
    async def answer() -> int:
        return 42

    assert run_coroutine(answer()) == 42

    async def inside() -> int:
        return run_coroutine(answer())

    assert asyncio.run(inside()) == 42


def test_a_failing_model_is_a_failed_test_without_the_tokens():
    def build(name: str, source: KeychainCredentialSource) -> TestModel:
        msg = "refused rt"
        raise RuntimeError(msg)

    result = option(signed_in(), build=build).test()
    assert not result.ok
    assert "RuntimeError" in result.detail
