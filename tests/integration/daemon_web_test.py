"""The web head may run explicitly confirmed daemon persistence changes; REST and MCP may not."""

from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from heinzel.heads import api, web
from heinzel.heads._exposure import exposed
from heinzel.modules.system.daemon import DaemonService
from heinzel.modules.system.daemon._models import Installed, Uninstalled

pytestmark = pytest.mark.integration

TOKEN = "s3ssion-token-for-tests-that-is-long-enough"
BASE = "http://127.0.0.1:8000"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def kernel(make_kernel):
    return make_kernel(builtins=True)


def test_only_web_and_cli_expose_daemon_persistence_operations(kernel):
    cli = {item.name for item in exposed(kernel, "cli")}
    on_the_web = {item.name for item in exposed(kernel, web.HEAD)}
    on_rest = {item.name for item in exposed(kernel, api.HEAD)}
    mcp = {item.name for item in exposed(kernel, "mcp")}
    for operation in ("system_daemon_install", "system_daemon_uninstall"):
        assert operation in cli
        assert operation in on_the_web
        assert operation not in on_rest
        assert operation not in mcp


@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_confirmed_daemon_persistence_action_is_authenticated_and_runs_on_web_head(kernel, monkeypatch, action):
    service = kernel.resolve(DaemonService)
    calls = []
    if action == "install":

        def install(interval, *, dry_run=False):
            calls.append(("install", interval, dry_run))
            return Installed(
                platform="darwin",
                executable=Path("/usr/local/bin/hz"),
                interval_minutes=5,
                files={},
                commands=[["launchctl", "load"]],
                dry_run=False,
            )

        monkeypatch.setattr(service, "install", install)
        expected = {"platform": "darwin", "interval_minutes": 5, "dry_run": False}
    else:

        def uninstall():
            calls.append(("uninstall",))
            return Uninstalled(removed=[Path("~/Library/LaunchAgents/hz.plist")], commands=[["launchctl", "unload"]])

        monkeypatch.setattr(service, "uninstall", uninstall)
        expected = {"removed": ["~/Library/LaunchAgents/hz.plist"]}

    client = TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url=BASE)
    path = f"/api/system/daemon/{action}"
    assert client.post(path).status_code == 401
    assert calls == []

    response = client.post(path, headers=AUTH)
    assert response.status_code == 200
    for key, value in expected.items():
        assert response.json()[key] == value
    expected_calls = [("install", timedelta(minutes=5), False)] if action == "install" else [("uninstall",)]
    assert calls == expected_calls

    rest = TestClient(api.build(kernel, port=8000), base_url=BASE)
    assert rest.post(f"/system/daemon/{action}").status_code == 404


def test_web_openapi_describes_confirmed_daemon_install_and_uninstall(kernel):
    paths = web.api_app(kernel, port=8000, token=TOKEN).openapi()["paths"]
    assert set(paths["/system/daemon/install"]) == {"post"}
    assert set(paths["/system/daemon/uninstall"]) == {"post"}
