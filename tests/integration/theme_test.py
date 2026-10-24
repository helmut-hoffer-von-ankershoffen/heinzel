"""Themes end to end (ADR 0044): plugins offer them, the CLI and the web head list them, and the pick round-trips.

The Acme test plugins in `tests/fixtures/layers` are real packages: the company `acme` ships `themes/acme.toml`, and
the team `acme-payments-checkout` offers a theme of the same name, which wins because the team is the more specific
layer. Nothing reaches the network.
"""

import importlib
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from heinzel import Config, Kernel, Module, ModuleRecord, Settings, Theme
from heinzel.heads import web
from heinzel.heads.cli import build
from heinzel.kernel import DEFAULT_THEME, Origin, discover, user_config_dir, walk

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parents[1] / "fixtures" / "layers"
ACME = ("heinzel_acme", "heinzel_acme_payments", "heinzel_acme_lisbon", "heinzel_acme_payments_checkout")
TOKEN = "s3ssion-token-for-tests"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def acme(monkeypatch, settings: Settings):
    """Builds a kernel with the built-in modules, the Acme plugins asked for (all by default) and `extra` modules."""
    monkeypatch.syspath_prepend(str(FIXTURES))

    def make(*plugins: str, extra: tuple[Module, ...] = ()) -> Kernel:
        records = discover(plugins=False)[0]
        for name in plugins or ACME:
            origin = Origin(distribution=name.replace("_", "-"), version="1.0.0", builtin=False)
            records += walk(importlib.import_module(name), origin)
        test = Origin(distribution="heinzel-test", version="1.0.0", builtin=False)
        records += [ModuleRecord(qualname=module.name, module=module, origin=test) for module in extra]
        return Kernel(settings, records, config=Config(files=(), data={}))

    yield make
    for name in ACME:  # imported afresh by the next test, so no test sees another's modules
        sys.modules.pop(name, None)


def hz(kernel: Kernel, *args: str, json_output: bool = True) -> tuple[int, object]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"] if json_output else list(args))
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, result.output


def outsider() -> Module:
    """A plugin outside every layer that offers an `acme` theme too: any layer in the stack beats it."""
    module = Module("lookalike", "Not a layer.")
    module.theme(Theme.model_validate(DEFAULT_THEME.model_dump() | {"name": "acme", "title": "Lookalike"}))
    return module


def test_theme_list_shows_the_default_and_each_plugins_theme_with_every_token(acme):
    code, listed = hz(acme("heinzel_acme"), "system", "theme", "list")
    assert code == 0, listed
    assert listed["selected"] == "default"
    default, company = listed["themes"]
    assert (default["name"], default["module"], default["plugin"], default["selected"]) == (
        "default",
        None,
        None,
        True,
    )
    assert default["light"] == DEFAULT_THEME.light.model_dump()
    assert (company["name"], company["title"], company["plugin"], company["selected"]) == (
        "acme",
        "Acme",
        "heinzel-acme",
        False,
    )
    assert company["light"]["primary"] == "#0f766e"
    assert company["dark"]["primary"] == "#2dd4bf"
    assert (company["radius"], company["border_width"]) == ("0.75rem", "1px")
    assert company["font_mono"] == 'ui-monospace, "SF Mono", monospace'
    assert listed["next"] == ["hz system theme use acme"]


def test_the_more_specific_layer_wins_a_theme_name_and_a_layer_beats_a_plugin_outside_the_stack(acme):
    code, listed = hz(acme(extra=(outsider(),)), "system", "theme", "list")
    assert code == 0, listed
    (theme,) = [t for t in listed["themes"] if t["name"] == "acme"]
    assert (theme["title"], theme["module"]) == ("Acme Checkout", "acme-payments-checkout")
    assert theme["shadows"] == ["acme", "lookalike"]


def test_a_plugin_outside_every_layer_offers_its_theme_when_no_layer_does(acme):
    code, listed = hz(acme("heinzel_acme_lisbon", extra=(outsider(),)), "system", "theme", "list")
    assert code == 0, listed
    assert [(t["name"], t["title"]) for t in listed["themes"]] == [("default", "Default"), ("acme", "Lookalike")]


def test_theme_use_writes_the_user_setting_and_the_list_follows(acme):
    kernel = acme("heinzel_acme")
    code, used = hz(kernel, "system", "theme", "use", "acme")
    assert code == 0, used
    assert used["selected"] == "acme"
    assert [t["name"] for t in used["themes"] if t["selected"]] == ["acme"]
    assert '[system.theme]\nname = "acme"' in (user_config_dir() / "config.toml").read_text(encoding="utf-8")
    assert hz(kernel, "system", "theme", "list")[1]["selected"] == "acme"
    assert hz(kernel, "system", "theme", "use", "default")[1]["selected"] == "default"


def test_theme_use_refuses_a_theme_nobody_offers_and_writes_nothing(acme):
    code, output = hz(acme("heinzel_acme"), "system", "theme", "use", "nope", json_output=False)
    assert code != 0
    assert "No theme is called 'nope'; available: default, acme" in " ".join(str(output).split())
    assert not (user_config_dir() / "config.toml").exists()


def test_a_theme_whose_plugin_is_gone_falls_back_to_the_default_and_says_so(acme):
    hz(acme("heinzel_acme"), "system", "theme", "use", "acme")
    sys.modules.pop("heinzel_acme", None)
    code, listed = hz(acme("heinzel_acme_lisbon"), "system", "theme", "list")
    assert code == 0, listed
    assert listed["selected"] == "default"
    assert "No installed plugin offers the theme 'acme'" in listed["note"]
    assert listed["next"] == ["hz system theme use default"]


def test_the_environment_decides_and_the_list_says_so(acme, monkeypatch):
    monkeypatch.setenv("HZ_SYSTEM_THEME__NAME", "acme")
    code, listed = hz(acme("heinzel_acme"), "system", "theme", "list")
    assert code == 0, listed
    assert listed["selected"] == "acme"
    assert "HZ_SYSTEM_THEME__NAME=acme in your environment decides" in listed["note"]
    assert listed["next"] == ["unset HZ_SYSTEM_THEME__NAME"]


def test_a_project_file_cannot_pick_the_theme(acme, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hz.toml").write_text('[system.theme]\nname = "acme"\n', encoding="utf-8")
    assert hz(acme("heinzel_acme"), "system", "theme", "list")[1]["selected"] == "default"


def test_the_cli_renders_the_themes_with_the_selected_one_marked(acme):
    code, output = hz(acme(extra=(outsider(),)), "system", "theme", "list", json_output=False)
    assert code == 0, output
    text = " ".join(str(output).split())
    assert "* default Default built in" in text
    assert "acme Acme Checkout heinzel-acme-payments-checkout" in text
    assert "acme hides the one in acme, lookalike" in text
    assert "hz system theme use acme" in text


def test_the_web_head_lists_the_themes_and_the_pick_round_trips(acme):
    client = TestClient(
        web.build(acme("heinzel_acme"), port=8000, token=TOKEN, static=None), base_url="http://127.0.0.1:8000"
    )
    listed = client.get("/api/system/theme/list", headers=AUTH).json()
    assert set(listed) == {"selected", "themes", "note", "next"}
    assert set(listed["themes"][0]) == {
        "name",
        "title",
        "module",
        "plugin",
        "selected",
        "light",
        "dark",
        "font_sans",
        "font_mono",
        "radius",
        "border_width",
        "shadows",
    }
    assert client.get("/api/system/theme/list").status_code == 401
    used = client.post("/api/system/theme/use", params={"name": "acme"}, headers=AUTH)
    assert used.status_code == 200, used.text
    assert used.json()["selected"] == "acme"
    assert client.get("/api/system/theme/list", headers=AUTH).json()["selected"] == "acme"  # the running server knows
    (user_config_dir() / "config.toml").write_text('[system.theme]\nname = "default"\n', encoding="utf-8")
    assert client.get("/api/system/theme/list", headers=AUTH).json()["selected"] == "default"  # switched elsewhere
    refused = client.post("/api/system/theme/use", params={"name": "nope"}, headers=AUTH)
    assert refused.status_code >= 400
    assert "No theme is called 'nope'" in refused.text
