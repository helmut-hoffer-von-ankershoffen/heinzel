"""Startup access adapters must not pair a fresh credential with stale configuration."""

from unittest.mock import Mock

import pytest
from pydantic_ai.models.test import TestModel

from heinzel import Config, Settings
from heinzel.modules.ai.access._gateway import Finished, GatewayAccess
from heinzel.modules.ai.access._models import AccessSettings
from heinzel.modules.ai.access._pydantic import PydanticAiAccess

pytestmark = pytest.mark.unit


MODEL = AccessSettings(model="example-model", base_url="https://old.example.test/v1", api="openai")
GATEWAY = AccessSettings(
    gateway_status_command=["example", "status"],
    gateway_test_command=["example", "ping"],
    gateway_env=["EXAMPLE_TOKEN"],
)


def model_access(current, credentials=None, get=None):
    credentials = credentials or Mock()
    credentials.get.return_value = "replacement-key"
    build = Mock(return_value=TestModel(custom_output_text="OK"))
    get = get or Mock(return_value=b'{"data": []}')
    access = PydanticAiAccess(
        MODEL,
        credentials,
        Config.empty().settings(Settings, "hz"),
        build=build,
        get=get,
        installed=lambda _: True,
        current_settings=lambda: current[0],
        binding=lambda: "dummy",  # Named probes do not require selecting the provider.
    )
    return access, credentials, get, build


@pytest.mark.parametrize("operation", ["status", "test"])
@pytest.mark.parametrize(
    "changed",
    [
        {"model": "new-model"},
        {"base_url": "https://new.example.test/v1"},
        {"api": "anthropic"},
    ],
)
def test_model_drift_blocks_credentials_status_and_network(operation, changed):
    access, credentials, get, build = model_access([MODEL.model_copy(update=changed)])
    status = Mock(wraps=access.status)
    access.status = status
    result = getattr(access, operation)()
    assert "settings changed" in result.detail
    assert result.fix == "Restart Heinzel before testing."
    credentials.get.assert_not_called()
    get.assert_not_called()
    build.assert_not_called()
    if operation == "test":
        assert not result.ok
        status.assert_not_called()
    else:
        assert not result.installed


@pytest.mark.parametrize("read", [1, 2])
def test_model_drift_during_either_credential_read_blocks_network(read):
    current = [MODEL]
    access, credentials, get, build = model_access(current)
    count = 0

    def key(_):
        nonlocal count
        count += 1
        if count == read:
            current[0] = MODEL.model_copy(update={"base_url": "https://new.example.test/v1"})
        return "replacement-key"

    credentials.get.side_effect = key
    result = access.test()
    assert not result.ok
    assert "settings changed" in result.detail
    get.assert_not_called()
    build.assert_not_called()


def test_model_drift_after_status_blocks_discovery():
    current = [MODEL]
    access, _, get, build = model_access(current)
    status = access.status

    def changed_status():
        result = status()
        current[0] = MODEL.model_copy(update={"base_url": "https://new.example.test/v1"})
        return result

    access.status = changed_status
    assert not access.test().ok
    get.assert_not_called()
    build.assert_not_called()


def test_model_drift_during_discovery_blocks_the_model_request():
    current = [MODEL]

    def discover(*_):
        current[0] = MODEL.model_copy(update={"model": "new-model"})
        return b'{"data": []}'

    access, _, _, build = model_access(current, get=Mock(side_effect=discover))
    assert not access.test().ok
    build.assert_not_called()


def test_unchanged_model_and_key_only_changes_work_without_selecting_or_restarting():
    current = [MODEL.model_copy(update={"gateway_title": "Another gateway", "plugin_model": "dummy-42"})]
    access, credentials, get, build = model_access(current)
    assert access.test().ok
    credentials.get.return_value = "rotated-key"
    assert access.test().ok
    assert [call.args[1] for call in build.call_args_list] == ["replacement-key", "rotated-key"]
    assert get.call_count == 2


@pytest.mark.parametrize("operation", ["status", "test"])
@pytest.mark.parametrize(
    "changed",
    [
        {"gateway_status_command": ["new", "status"]},
        {"gateway_test_command": ["new", "ping"]},
        {"gateway_env": ["NEW_TOKEN"]},
    ],
)
def test_gateway_drift_blocks_resolution_and_processes(operation, changed):
    run, which = Mock(), Mock()
    access = GatewayAccess(GATEWAY, run=run, which=which, current_settings=lambda: GATEWAY.model_copy(update=changed))
    result = getattr(access, operation)()
    assert "changed" in result.detail
    assert result.fix == "Restart Heinzel before testing."
    run.assert_not_called()
    which.assert_not_called()


@pytest.mark.parametrize("operation", ["status", "test"])
def test_gateway_drift_during_resolution_blocks_processes(operation):
    current = [GATEWAY]

    def resolve(_):
        current[0] = GATEWAY.model_copy(update={"gateway_test_command": ["new", "ping"]})
        return "/bin/example"

    run = Mock()
    access = GatewayAccess(GATEWAY, run=run, which=resolve, current_settings=lambda: current[0])
    assert "changed" in getattr(access, operation)().detail
    run.assert_not_called()


def test_unchanged_gateway_ignores_other_provider_and_display_settings():
    current = GATEWAY.model_copy(update={"model": "new-model", "gateway_title": "New title"})
    run = Mock(return_value=Finished(0, "OK", ""))
    access = GatewayAccess(GATEWAY, run=run, which=lambda _: "/bin/example", current_settings=lambda: current)
    assert access.status().installed
    assert access.test().ok
    assert [call.args[0] for call in run.call_args_list] == [["/bin/example", "status"], ["/bin/example", "ping"]]


def test_default_settings_readers_notice_saved_changes_before_side_effects(monkeypatch):
    monkeypatch.setenv("HZ_AI_ACCESS__MODEL", MODEL.model)
    monkeypatch.setenv("HZ_AI_ACCESS__BASE_URL", MODEL.base_url)
    monkeypatch.setenv("HZ_AI_ACCESS__API", MODEL.api)
    monkeypatch.setenv("HZ_AI_ACCESS__GATEWAY_TEST_COMMAND", '["example", "ping"]')
    settings = Config.load().settings(AccessSettings, "ai.access")
    credentials, run, which, build, get = Mock(), Mock(), Mock(), Mock(), Mock()
    model = PydanticAiAccess(settings, credentials, Config.empty().settings(Settings, "hz"), build=build, get=get)
    gateway = GatewayAccess(settings, run=run, which=which)
    monkeypatch.setenv("HZ_AI_ACCESS__BASE_URL", "https://new.example.test/v1")
    monkeypatch.setenv("HZ_AI_ACCESS__GATEWAY_TEST_COMMAND", '["new", "ping"]')
    assert not model.test().ok
    assert not gateway.test().ok
    for seam in (credentials.get, run, which, build, get):
        seam.assert_not_called()
