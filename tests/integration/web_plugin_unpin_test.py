"""The Updates pane's authenticated endpoint changes only the pin it previewed."""

import tomllib
from functools import partial

import pytest
from fastapi.testclient import TestClient

from heinzel.heads import web
from heinzel.kernel import Installation
from heinzel.modules.system.plugin._service import PluginService
from tests.unit.modules.plugin_unpin_test import NAME, OTHER, isolated_python, pinned, tool_env

__all__ = ["isolated_python", "pinned", "tool_env"]
pytestmark = pytest.mark.integration
TOKEN = "t" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}
UNPIN = "/api/system/plugin/unpin"


def test_web_unpin_is_confirmed_and_persists_on_reopen(pinned, make_kernel, monkeypatch):
    service, installation, receipt, payload, lifecycle = pinned
    monkeypatch.setattr(web, "AutoUpdater", partial(web.AutoUpdater, development=None))

    def opened():
        kernel = make_kernel(builtins=True)
        kernel.bind(Installation, installation)
        kernel.bind(PluginService, service)
        return TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url="http://127.0.0.1:8000")

    def held(client):
        status = client.get("/api/auto-update", headers=AUTH).json()
        assert status["enabled"] is False
        return status["holds"]

    before, code = receipt.read_bytes(), payload.read_bytes()
    requested = {"distribution": NAME}
    with opened() as client:
        assert {item["name"] for item in held(client)} == {NAME, OTHER}
        assert client.post(UNPIN, params=requested).status_code == 401
        preview = client.post(UNPIN, params=requested, headers=AUTH).json()
        assert not preview["done"]
        assert preview["version"] == "1.0.0"
        assert receipt.read_bytes() == before
        rejected = client.post(UNPIN, params={**requested, "confirm": "stale-preview"}, headers=AUTH)
        assert rejected.status_code == 400
        assert "preview the unpin again" in rejected.json()["error"]
        assert receipt.read_bytes() == before
        assert not lifecycle
        confirmed = client.post(UNPIN, params={**requested, "confirm": preview["confirmation"]}, headers=AUTH)
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["done"]

    with opened() as client:
        assert {item["name"] for item in held(client)} == {OTHER}
    expected = tomllib.loads(before.decode())
    del expected["tool"]["requirements"][1]["specifier"]
    assert tomllib.loads(receipt.read_text()) == expected
    assert payload.read_bytes() == code
    assert [(event.action, event.distribution) for event in service.history()] == [("unpin", NAME)]
    assert installation.startup_state_matches()
