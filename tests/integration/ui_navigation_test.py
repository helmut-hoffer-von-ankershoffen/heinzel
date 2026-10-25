"""Every authorized head sees provider navigation and private actions use the same flat JSON wire."""

import logging
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typer.testing import CliRunner

from heinzel import CurrentUser, Module, ModuleSettings, NavSettings, RunOperation
from heinzel.heads import api, cli
from heinzel.heads.api._auth import ApiBearer
from heinzel.modules.system.ui import UiService

pytestmark = pytest.mark.integration
NOW = datetime(2026, 1, 1, tzinfo=UTC)


class HarborSettings(ModuleSettings):
    enabled: bool = True


def provider():
    module = Module("harbor", "Harbor tools.")
    module.settings(HarborSettings)

    @module.operation(private=True, read_only=False)
    def change(message: str) -> str:
        """Return the private text."""
        return message

    effect = RunOperation(operation="change", arguments={"message": "private-sensitive-message"})
    module.nav(title="Harbor", icon="cloud-sun", settings=NavSettings(sections=("harbor",)), sidebar=effect)
    module.action("change", label="Change", icon="code", effect=effect)
    return module


def test_navigation_is_private_and_shared_by_cli_and_authenticated_api(make_kernel):
    kernel = make_kernel(builtins=True, harbor=provider())
    public = TestClient(api.build(kernel), base_url="http://localhost")
    assert public.get("/system/ui/navigation").status_code == 404
    user = kernel.resolve(CurrentUser)
    token = ApiBearer.issue(user.owner(), "test", now=NOW)
    client = TestClient(
        api.build(kernel, credentials=token, audience="test", clock=lambda: NOW), base_url="http://localhost"
    )
    assert client.get("/system/ui/navigation").status_code == 401
    response = client.get("/system/ui/navigation", headers={"Authorization": f"Bearer {token.secret}"})
    assert response.status_code == 200
    assert next(entry for entry in response.json() if entry["module"] == "harbor")["icon"] == "cloud-sun"
    result = CliRunner().invoke(cli.build(kernel), ["system", "ui", "navigation"])
    assert result.exit_code == 0, result.output
    assert "harbor:main" in result.output
    assert "cloud-sun" in result.output


def test_generic_private_action_uses_direct_operation_json_body_without_sensitive_url_or_logs(make_kernel, caplog):
    kernel = make_kernel(builtins=True, harbor=provider())
    user = kernel.resolve(CurrentUser)
    token = ApiBearer.issue(user.owner(), "test", now=NOW)
    client = TestClient(
        api.build(kernel, credentials=token, audience="test", clock=lambda: NOW), base_url="http://localhost"
    )
    headers = {"Authorization": f"Bearer {token.secret}"}
    service = kernel.resolve(UiService)
    action = next(entry for entry in service.actions() if entry.module == "harbor")
    nav = next(entry for entry in service.navigation() if entry.module == "harbor")
    assert action.effect.call == nav.sidebar.call
    call = action.effect.call
    assert call.method == "POST"
    assert call.query == {}
    assert call.body == {"message": "private-sensitive-message"}
    with caplog.at_level(logging.INFO):
        response = client.request(call.method, call.path, params=call.query, json=call.body, headers=headers)
        assert response.status_code == 200
        assert response.json() == call.body["message"]
        assert call.body["message"] not in str(response.request.url)
        assert client.post(call.path, headers=headers, json={**call.body, "extra": "refused"}).status_code == 422
    assert call.body["message"] not in caplog.text


class Appearance(BaseModel):
    hat: str
    accents: list[str]


def test_private_nested_arguments_are_validated_serialized_and_forwarded_as_one_flat_body(make_kernel):
    module = Module("harbor", "Harbor tools.")

    @module.operation(private=True, read_only=False)
    def appearance(value: Appearance) -> Appearance:
        """Choose an appearance."""
        return value

    value = {"hat": "blue", "accents": ["snow", "star"]}
    module.nav(title="Harbor", icon="code", sidebar=RunOperation(operation="appearance", arguments={"value": value}))
    kernel = make_kernel(builtins=True, harbor=module)
    user = kernel.resolve(CurrentUser)
    token = ApiBearer.issue(user.owner(), "test", now=NOW)
    client = TestClient(
        api.build(kernel, credentials=token, audience="test", clock=lambda: NOW), base_url="http://localhost"
    )
    entry = next(entry for entry in kernel.resolve(UiService).navigation() if entry.module == "harbor")
    call = entry.sidebar.call
    assert call.query == {}
    assert call.body == {"value": value}
    response = client.request(
        call.method, call.path, json=call.body, headers={"Authorization": f"Bearer {token.secret}"}
    )
    assert response.status_code == 200
    assert response.json() == value
