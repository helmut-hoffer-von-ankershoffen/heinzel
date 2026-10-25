"""`hz system config reveal` through the CLI and web heads, and the heads that must not offer it.

The opener is faked: nothing really opens.
"""

import json
import shutil

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from heinzel.heads import api, web
from heinzel.heads._exposure import exposed
from heinzel.heads.cli import build
from heinzel.kernel import FILENAME, user_config_dir
from heinzel.modules.system.config import ConfigService
from heinzel.modules.system.config._reveal import Revealer

pytestmark = pytest.mark.integration

TOKEN = "s3ssion-token-for-tests-that-is-long-enough"
BASE = "http://127.0.0.1:8000"
PATH = ("system", "config", "reveal")


@pytest.fixture
def kernel(make_kernel):
    kernel = make_kernel(builtins=True)
    commands: list[list[str]] = []
    kernel.resolve(ConfigService).revealer = Revealer(
        "darwin", lambda name: f"/usr/bin/{name}", {}, lambda command: commands.append(list(command))
    )
    kernel.commands = commands  # type: ignore[attr-defined] - for the assertions
    return kernel


def test_the_cli_shows_the_user_file_in_finder(kernel):
    result = CliRunner().invoke(build(kernel), [*PATH, "user", "--json"])
    assert result.exit_code == 0, result.output
    revealed = json.loads(result.output)
    path = user_config_dir() / FILENAME
    assert (revealed["scope"], revealed["path"], revealed["supported"]) == ("user", str(path), True)
    assert revealed["opened"] is True
    folder = next(d for d in path.parents if d.is_dir())
    assert kernel.commands == [["/usr/bin/open", str(folder)]]
    assert not path.exists()


def test_the_web_head_reveals_behind_its_token(kernel):
    client = TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url=BASE)
    assert client.post("/api/system/config/reveal", params={"scope": "user"}).status_code == 401
    assert kernel.commands == []
    answer = client.post(
        "/api/system/config/reveal", params={"scope": "user"}, headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert answer.status_code == 200
    assert answer.json()["opened"] is True
    assert len(kernel.commands) == 1


def test_the_web_head_refuses_a_scope_that_isnt_one(kernel):
    client = TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url=BASE)
    answer = client.post(
        "/api/system/config/reveal", params={"scope": "/etc"}, headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert answer.status_code == 422
    assert kernel.commands == []


def test_the_show_report_tells_the_web_app_whether_and_how_to_offer_it(kernel):
    client = TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url=BASE)
    report = client.get("/api/system/config/show", headers={"Authorization": f"Bearer {TOKEN}"}).json()
    assert (report["can_reveal"], report["file_manager"]) == (True, "Finder")


def test_neither_the_rest_head_nor_mcp_offer_it(kernel):
    assert not any(item.path == PATH for item in exposed(kernel, api.HEAD))
    assert not any(item.path == PATH for item in exposed(kernel, "mcp"))
    assert any(item.path == PATH for item in exposed(kernel, "web"))
    rest = TestClient(api.build(kernel, port=8000), base_url=BASE)
    assert rest.post("/system/config/reveal", params={"scope": "user"}).status_code in {404, 405}
    assert kernel.commands == []


def test_the_real_opener_is_resolved_on_this_machine(make_kernel):
    service = make_kernel(builtins=True).resolve(ConfigService)
    assert service.revealer.which is shutil.which
