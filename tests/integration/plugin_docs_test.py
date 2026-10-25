"""`hz system plugin docs` through the CLI head: the model as JSON, and the pages a repository commits (ADR 0082).

The Acme test plugin in `tests/fixtures/layers` is a real package with a layer, an adapter, a playbook and a theme.
"""

import importlib
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from heinzel import Config, CurrentUser, Kernel, Settings
from heinzel.heads import api
from heinzel.heads.api._auth import ApiBearer
from heinzel.heads.cli import build
from heinzel.kernel import Origin, discover, walk

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parents[1] / "fixtures" / "layers"


@pytest.fixture
def kernel(monkeypatch, settings: Settings) -> Kernel:
    monkeypatch.syspath_prepend(str(FIXTURES))
    records = discover(plugins=False)[0]
    records += walk(
        importlib.import_module("heinzel_acme"), Origin(distribution="heinzel-acme", version="1.0.0", builtin=False)
    )
    yield Kernel(settings, records, config=Config(files=(), data={}))
    sys.modules.pop("heinzel_acme", None)


def hz(kernel: Kernel, *args: str) -> tuple[int, object]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, result.output


def test_show_describes_the_plugin_in_every_head(kernel):
    code, docs = hz(kernel, "system", "plugin", "docs", "show", "--plugin", "acme")
    assert code == 0, docs
    (page,) = docs["pages"]
    assert page["distribution"] == "heinzel-acme"
    assert page["kinds"] == ["connector", "theme", "playbook", "layer"]
    assert page["adapters"] == [
        {
            "adapter": "acme",
            "capability": "tracker",
            "modes": ["read"],
            "hosts": [],
            "hosts_from_settings": False,
            "binding": "hz system config set capabilities.tracker acme",
        }
    ]
    assert [theme["name"] for theme in page["themes"]] == ["acme"]
    assert page["layers"] == [{"module": "acme", "parent": None, "site": None, "roles": []}]
    assert page["playbooks"][0]["defaults"] == {"capabilities.tracker": "acme", "capabilities.calendar": "acme"}
    (scene,) = page["scenes"]
    assert scene["commands"][0] == "hz acme --help"


def test_the_same_documentation_is_available_only_through_authenticated_network_heads(kernel):
    credential = ApiBearer.issue(kernel.resolve(CurrentUser).owner(), "docs-test")
    private = TestClient(api.build(kernel, credentials=credential, audience="docs-test"), base_url="http://localhost")
    path = "/system/plugin/docs/show"
    assert private.get(path, params={"plugin": "acme"}).status_code == 401
    response = private.get(path, params={"plugin": "acme"}, headers={"Authorization": f"Bearer {credential.secret}"})
    assert response.status_code == 200
    code, cli_documentation = hz(kernel, "system", "plugin", "docs", "show", "--plugin", "acme")
    assert code == 0
    assert response.json() == cli_documentation
    assert TestClient(api.build(kernel), base_url="http://localhost").get(path).status_code == 404


def test_build_writes_the_pages_and_check_finds_them_current(kernel, tmp_path):
    code, result = hz(kernel, "system", "plugin", "docs", "build", "--directory", str(tmp_path), "--plugin", "acme")
    assert code == 0, result
    assert result["written"] == ["heinzel-acme/index.md", "heinzel-acme/assets/theme-acme.svg", "README.md"]
    page = (tmp_path / "heinzel-acme" / "index.md").read_text(encoding="utf-8")
    assert "# heinzel-acme" in page
    assert "## Themes" in page
    assert "## Layers" in page
    assert (tmp_path / "heinzel-acme" / "assets" / "theme-acme.svg").read_text(encoding="utf-8").startswith("<svg")
    code, check = hz(
        kernel, "system", "plugin", "docs", "build", "--directory", str(tmp_path), "--plugin", "acme", "--check"
    )
    assert code == 0
    assert (check["stale"], check["written"], check["stale_pages"]) == (False, [], [])
    (tmp_path / "heinzel-acme" / "index.md").write_text("stale", encoding="utf-8")
    code, output = hz(
        kernel, "system", "plugin", "docs", "build", "--directory", str(tmp_path), "--plugin", "acme", "--check"
    )
    assert code == 1, "`make check` relies on the exit code"
    assert "1 page(s) out of date" in str(output)
    assert "heinzel-acme/index.md" in str(output)


def test_an_unknown_plugin_fails_with_what_is_installed(kernel):
    code, output = hz(kernel, "system", "plugin", "docs", "show", "--plugin", "nope")
    assert code == 1
    assert "No plugin 'nope' is installed" in str(output)
    assert "acme" in str(output)
