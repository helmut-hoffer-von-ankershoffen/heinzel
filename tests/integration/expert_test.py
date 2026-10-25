"""Expert mode across heads (ADR 0041): the web app reads and switches it through the API; machines see everything."""

import pytest
from fastapi.testclient import TestClient

from heinzel.heads import api, web
from heinzel.heads._exposure import exposed

pytestmark = pytest.mark.integration

TOKEN = "s3ssion-token-for-tests"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(make_kernel, tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    kernel = make_kernel(builtins=True)
    return TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url="http://127.0.0.1:8000")


def test_the_web_api_reads_the_mode_and_every_operations_audience(client):
    mode = client.get("/api/system/expert", headers=AUTH).json()
    assert mode["expert"] is False
    audiences = {(o["module"], o["operation"]): o["audience"] for o in mode["operations"]}
    assert audiences[("system.doctor", "doctor")] == "everyday"
    assert audiences[("heinzel", "graph")] == "expert"
    assert audiences[("system.expert", "on")] == "everyday"


def test_the_web_api_switches_the_mode_with_the_same_operations(client, tmp_path):
    assert client.post("/api/system/expert/on", headers=AUTH).json()["expert"] is True
    assert client.get("/api/system/expert", headers=AUTH).json()["expert"] is True  # the running server knows
    assert "expert = true" in (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert client.post("/api/system/expert/off", headers=AUTH).json()["expert"] is False


def test_the_running_web_head_sees_a_mode_switched_elsewhere(client, tmp_path):
    assert client.get("/api/system/expert", headers=AUTH).json()["expert"] is False
    (tmp_path / "config.toml").write_text("[hz]\nexpert = true\n", encoding="utf-8")  # `hz system expert on` elsewhere
    assert client.get("/api/system/expert", headers=AUTH).json()["expert"] is True


def test_machine_heads_list_expert_operations_outside_expert_mode(make_kernel):
    kernel = make_kernel(builtins=True)
    assert not kernel.settings.expert
    for head in ("mcp", "api", "web"):
        names = {item.name for item in exposed(kernel, head)}
        assert {"system_info", "system_config_show", "system_expert"} <= names
        assert ("heinzel_graph" in names) is (head != "api")  # the module's HTTP API is the web app's (ADR 0051)
    paths = {route.path for route in api.build(kernel).routes}  # type: ignore[attr-defined]
    assert {"/system/info", "/system/expert", "/system/expert/on"} <= paths
