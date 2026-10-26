"""The resource meters consume one typed cached sample through CLI, MCP and the API."""

import json
from types import SimpleNamespace

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from typer.testing import CliRunner

from heinzel.heads import api, mcp, web
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration


def test_resource_sample_is_shared_by_all_heads(make_kernel, monkeypatch):
    # Exercise several transports and a real MCP session without racing the production one-second cache window.
    monkeypatch.setattr("heinzel.modules.system.resource.CACHE_SECONDS", 60.0)
    monkeypatch.setattr("heinzel.modules.system.resource.psutil.cpu_percent", lambda **_: 12.5)
    monkeypatch.setattr("heinzel.modules.system.resource.psutil.virtual_memory", lambda: SimpleNamespace(percent=40))
    monkeypatch.setattr("heinzel.modules.system.resource.psutil.getloadavg", lambda: (21.0, 19.0, 18.0))
    monkeypatch.setattr("heinzel.modules.system.resource.psutil.cpu_count", lambda: 10)
    kernel = make_kernel(builtins=True)
    result = CliRunner().invoke(build(kernel), ["system", "resource", "--json"])
    assert result.exit_code == 0, result.output
    expected = json.loads(result.stdout)
    client = TestClient(api.build(kernel), base_url="http://localhost")
    assert client.get("/system/resource").json() == expected
    token = "t" * 43
    ui = TestClient(web.build(kernel, port=8000, token=token, static=None), base_url="http://127.0.0.1:8000")
    assert ui.get("/api/system/resource").status_code == 401
    result = ui.get("/api/system/resource", headers={"Authorization": f"Bearer {token}"})
    assert result.headers["Cache-Control"] == "no-store"
    assert result.json() == expected

    async def read():
        async with Client(mcp.build(kernel)) as connected:
            result = await connected.call_tool("system_resource")
            assert not result.is_error
            return json.loads(result.content[0].text)

    assert anyio.run(read) == expected
    assert expected["load_1m"] > 2 * expected["logical_cores"]
