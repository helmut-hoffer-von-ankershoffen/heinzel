"""`hz system notify` through the CLI head, and which heads expose it (ADR 0043).

Every directory is a temporary one (heinzel.testing); the native notifier is never reached: `test` only records.
"""

import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from heinzel.heads import api, web
from heinzel.heads._exposure import exposed
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

runner = CliRunner()
TOKEN = "s3ssion-token-for-tests"
BASE = "http://127.0.0.1:8000"


@pytest.fixture
def kernel(make_kernel):
    return make_kernel(builtins=True)


def hz(kernel, *args: str) -> str:
    result = runner.invoke(build(kernel), list(args))
    assert result.exit_code == 0, result.output
    return result.output


def test_a_test_notice_is_listed_until_someone_shows_it(kernel):
    hz(kernel, "system", "notify", "test")
    listed = json.loads(hz(kernel, "system", "notify", "list", "--json"))
    notice = listed["notices"][0]
    assert (notice["kind"], notice["popup"], notice["delivered_by"]) == ("test", True, None)
    assert listed["waiting"] == 1
    acked = json.loads(hz(kernel, "system", "notify", "ack", notice["id"], "--json"))
    assert acked == {"delivered": [notice["id"]]}
    assert "shown (web)" in hz(kernel, "system", "notify", "list")


def test_turned_off_it_says_how_to_turn_it_on(kernel, monkeypatch):
    monkeypatch.setenv("HZ_SYSTEM_NOTIFY__ENABLED", "false")
    result = runner.invoke(build(kernel), ["system", "notify", "test"])
    assert result.exit_code != 0
    assert "hz system config set system.notify.enabled true" in result.output


def test_notices_are_for_the_web_head_behind_its_token_not_the_unauthenticated_rest_head(kernel):
    assert not any(item.path[:2] == ("system", "notify") for item in exposed(kernel, api.HEAD))
    assert TestClient(api.build(kernel, port=8000), base_url=BASE).get("/system/notify/list").status_code == 404
    client = TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url=BASE)
    assert client.get("/api/system/notify/list").status_code == 401
    listed = client.get("/api/system/notify/list", headers={"Authorization": f"Bearer {TOKEN}"})
    assert listed.status_code == 200
    assert listed.json()["notices"] == []


def test_the_native_test_is_for_people_not_agents_over_mcp(kernel):
    assert not any(item.path == ("system", "notify", "test") for item in exposed(kernel, "mcp"))
    assert any(item.path == ("system", "notify", "test") for item in exposed(kernel, "web"))
