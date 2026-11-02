"""Themes end to end (ADR 0044): plugins offer them, the CLI and the web head list them, and the pick round-trips.

The Acme test plugins in `tests/fixtures/layers` are real packages: the company `acme` ships `themes/acme.toml`, and
the team `acme-payments-checkout` offers a theme of the same name, which wins because the team is the more specific
layer. Nothing reaches the network.
"""

import importlib
import json
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from heinzel import Config, HeinzelError, Kernel, Module, ModuleRecord, Settings, Theme, ThemeFonts, ThemeSignals
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
    assert ("acme", "Lookalike") in [(t["name"], t["title"]) for t in listed["themes"]]


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
    assert "HZ_SYSTEM_THEME__NAME in your environment decides the theme (acme)" in listed["note"]
    assert listed["next"] == ["unset HZ_SYSTEM_THEME__NAME"]


@pytest.mark.parametrize("where", ["environment", "file"])
def test_a_configured_name_that_is_no_theme_name_falls_back_to_the_default(acme, monkeypatch, where):
    if where == "environment":
        monkeypatch.setenv("HZ_SYSTEM_THEME__NAME", "Acme; }")
    else:
        user_config_dir().mkdir(parents=True, exist_ok=True)
        (user_config_dir() / "config.toml").write_text('[system.theme]\nname = "Acme"\n', encoding="utf-8")
    code, listed = hz(acme("heinzel_acme"), "system", "theme", "list")
    assert code == 0, listed
    assert listed["selected"] == "default"
    assert "isn't a theme name" in listed["note"]
    assert listed["next"] == (
        ["unset HZ_SYSTEM_THEME__NAME"] if where == "environment" else ["hz system theme use default"]
    )


def test_an_org_and_the_site_offering_one_name_is_a_conflict_nobody_decided(acme):
    code, listed = hz(acme(), "system", "theme", "list")
    assert code == 0, listed
    assert "acme-warm" not in [t["name"] for t in listed["themes"]]
    (conflict,) = listed["conflicts"]
    assert conflict.startswith("acme-warm: offered by acme-lisbon and acme-payments, an org and the site")
    assert "until acme-payments-checkout offers it" in conflict
    assert hz(acme(), "system", "theme", "use", "acme-warm")[0] != 0


def test_an_org_alone_offers_its_theme_when_the_site_is_not_in_the_stack(acme):
    code, listed = hz(acme("heinzel_acme", "heinzel_acme_payments"), "system", "theme", "list")
    assert code == 0, listed
    assert ("acme-warm", "Acme Payments warm") in [(t["name"], t["title"]) for t in listed["themes"]]
    assert listed["conflicts"] == []


def test_a_project_file_cannot_pick_the_theme(acme, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hz.toml").write_text('[system.theme]\nname = "acme"\n', encoding="utf-8")
    assert hz(acme("heinzel_acme"), "system", "theme", "list")[1]["selected"] == "default"


def test_the_cli_renders_the_themes_with_the_selected_one_marked(acme):
    code, output = hz(acme(extra=(outsider(),)), "system", "theme", "list", json_output=False)
    assert code == 0, output
    text = " ".join(str(output).split())
    assert "* default Built-in fallback built in" in text
    assert "acme Acme Checkout heinzel-acme-payments-checkout" in text
    assert "acme hides the one in acme, lookalike" in text
    assert "hz system theme use acme" in text


def test_the_web_head_lists_the_themes_and_the_pick_round_trips(acme):
    client = TestClient(
        web.build(acme("heinzel_acme"), port=8000, token=TOKEN, static=None), base_url="http://127.0.0.1:8000"
    )
    listed = client.get("/api/system/theme/list", headers=AUTH).json()
    assert set(listed) == {"selected", "themes", "conflicts", "note", "next"}
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
        "radius_control",
        "border_width",
        "font_size",
        "font_size_small",
        "icon_size",
        "row_height",
        "heading_weight",
        "heading_tracking",
        "spacing",
        "fonts",
        "logo",
        "variant",
        "shadows",
        "heinzel",
        "heinzel_assets",
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


# Type scale, density and font files (ADR 0044, amended 2026-10-24).

FONT = FIXTURES / "heinzel_acme" / "themes" / "fonts" / "acme-sans.woff2"


def web_client(kernel: Kernel) -> TestClient:
    return TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url="http://127.0.0.1:8000")


def test_the_list_carries_the_type_scale_density_icon_colours_and_fonts(acme):
    listed = web_client(acme("heinzel_acme")).get("/api/system/theme/list", headers=AUTH).json()
    default, company = listed["themes"]
    assert {key: default[key] for key in ("radius", "radius_control", "font_size", "font_size_small")} == {
        "radius": "0.75rem",
        "radius_control": "0.5rem",
        "font_size": "15px",
        "font_size_small": "13px",
    }
    assert (default["icon_size"], default["row_height"], default["spacing"]) == ("18px", "36px", "0.25rem")
    assert (default["heading_weight"], default["heading_tracking"], default["fonts"]) == (600, "0", [])
    assert (default["dark"]["accent_foreground"], default["dark"]["icon"]) == ("#e9ede5", "#b4c3b5")
    assert (company["font_size"], company["heading_tracking"]) == ("14px", "-0.5px")
    assert company["light"]["accent_foreground"] == company["light"]["foreground"]  # derived when not given
    (shipped,) = company["fonts"]
    assert set(shipped) == {"family", "url", "weight", "style"}
    assert (shipped["family"], shipped["weight"], shipped["style"]) == ("Acme Sans", "100 900", "normal")
    assert shipped["url"].startswith("/theme-fonts/acme/0.woff2?v=")


def test_the_web_head_serves_a_themes_font_without_the_token_with_its_type_and_forever(acme):
    client = web_client(acme("heinzel_acme"))
    url = client.get("/api/system/theme/list", headers=AUTH).json()["themes"][1]["fonts"][0]["url"]
    served = client.get(url)
    assert served.status_code == 200, served.text
    assert served.content == FONT.read_bytes()
    assert served.headers["content-type"] == "font/woff2"
    assert served.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert served.headers["x-content-type-options"] == "nosniff"
    assert "font-src 'self'" in served.headers["content-security-policy"]
    assert served.headers["cross-origin-resource-policy"] == "same-origin"
    assert client.get(url, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/theme-fonts/acme/1.woff2",
        "/theme-fonts/acme/-1.woff2",
        "/theme-fonts/acme/999.woff2",
        "/theme-fonts/acme/0.ttf",
        "/theme-fonts/acme/0",
        "/theme-fonts/acme/fonts%2Facme-sans.woff2",
        "/theme-fonts/acme/..%2F..%2Fthemes%2Facme.toml",
        "/theme-fonts/nope/0.woff2",
        "/theme-fonts/default/0.woff2",
        "/theme-fonts/Acme/0.woff2",
        "/theme-fonts/..%2Facme/0.woff2",
        "/theme-fonts/acme/0.woff2/x",
    ],
)
def test_the_web_head_refuses_a_font_no_theme_ships(acme, path):
    refused = web_client(acme("heinzel_acme")).get(path)
    assert refused.status_code == 404
    assert refused.headers["content-type"].startswith("application/json")
    assert refused.content != FONT.read_bytes()


def test_only_the_theme_that_wins_its_name_serves_fonts(acme):
    # The team's `acme` hides the company's, and the team ships no font file.
    client = web_client(acme())
    (theme,) = [t for t in client.get("/api/system/theme/list", headers=AUTH).json()["themes"] if t["name"] == "acme"]
    assert theme["fonts"] == []
    assert client.get("/theme-fonts/acme/0.woff2").status_code == 404


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "attacker.example.test:8000"},
        {"Host": "127.0.0.1:9999"},
        {"Origin": "http://attacker.example.test"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ],
)
def test_fonts_are_refused_to_another_host_or_site(acme, headers):
    refused = web_client(acme("heinzel_acme")).get("/theme-fonts/acme/0.woff2", headers=headers)
    assert refused.status_code == 403
    assert refused.content != FONT.read_bytes()


def test_no_fonts_are_served_when_the_theme_module_is_off(acme, monkeypatch):
    kernel = acme("heinzel_acme")
    client = web_client(kernel)
    monkeypatch.setattr(kernel, "provides", lambda kind: False)
    assert client.get("/theme-fonts/acme/0.woff2").status_code == 404


def test_no_fonts_are_served_when_the_theme_service_cannot_be_built(acme, monkeypatch):
    kernel = acme("heinzel_acme")

    resolve = kernel.resolve

    def unbuildable(kind: type) -> object:
        if kind is ThemeFonts:
            msg = f"No module provides {kind.__name__}"
            raise HeinzelError(msg)
        return resolve(kind)

    monkeypatch.setattr(kernel, "resolve", unbuildable)
    assert web_client(kernel).get("/theme-fonts/acme/0.woff2").status_code == 404


def test_a_font_file_removed_after_import_is_left_out_and_not_served(acme, tmp_path, monkeypatch):
    copy = tmp_path / "layers"
    shutil.copytree(FIXTURES, copy)
    monkeypatch.syspath_prepend(str(copy))
    kernel = acme("heinzel_acme")
    (copy / "heinzel_acme" / "themes" / "fonts" / "acme-sans.woff2").unlink()
    client = web_client(kernel)
    assert client.get("/api/system/theme/list", headers=AUTH).json()["themes"][1]["fonts"] == []
    assert client.get("/theme-fonts/acme/0.woff2").status_code == 404


# Logos and a main tint (ADR 0044, amended 2026-10-24).

LOGOS = FIXTURES / "heinzel_acme" / "themes" / "logo"
LOGO_HEADERS = {
    "content-type": "image/svg+xml",
    "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'",
    "x-content-type-options": "nosniff",
    "cache-control": "public, max-age=31536000, immutable",
}


def test_the_list_carries_each_themes_logo_and_main_tint(acme):
    code, listed = hz(acme("heinzel_acme"), "system", "theme", "list")
    assert code == 0, listed
    default, company = listed["themes"]
    assert set(default["logo"]) == {"light", "dark", "mark", "alt", "height"}
    assert (default["logo"]["alt"], default["logo"]["height"]) == ("Heinzelmännchen", "24px")
    for name in ("light", "dark", "mark"):
        assert default["logo"][name].startswith(f"/theme-assets/default/{name}.svg?v=")
    assert company["logo"]["light"].startswith("/theme-assets/acme/light.svg?v=")
    assert company["logo"]["dark"].startswith("/theme-assets/acme/dark.svg?v=")
    assert company["logo"]["mark"].startswith("/theme-assets/acme/mark.svg?v=")
    assert (company["logo"]["alt"], company["logo"]["height"]) == ("Acme", "28px")
    assert (company["light"]["main_tint"], company["dark"]["main_tint"]) == ("#f7f4ee", None)
    assert default["light"]["main_tint"] is None


def test_a_theme_without_a_logo_lists_none_and_one_without_dark_points_at_light(acme, tmp_path, monkeypatch):
    copy = tmp_path / "layers"
    shutil.copytree(FIXTURES, copy)
    monkeypatch.syspath_prepend(str(copy))
    theme_file = copy / "heinzel_acme" / "themes" / "acme.toml"
    toml = theme_file.read_text(encoding="utf-8")
    theme_file.write_text(toml.replace('dark = "logo/acme-dark.svg"\nmark = "logo/acme-mark.svg"\n', ""), "utf-8")
    listed = web_client(acme()).get("/api/system/theme/list", headers=AUTH).json()
    by_name = {theme["name"]: theme for theme in listed["themes"]}
    assert by_name["acme"]["logo"] is None  # the team's `acme` wins its name and ships no logo
    company = web_client(acme("heinzel_acme")).get("/api/system/theme/list", headers=AUTH).json()["themes"][1]
    assert company["logo"]["dark"] == company["logo"]["light"]
    assert company["logo"]["mark"] is None


@pytest.mark.parametrize(
    ("theme", "file"),
    [("acme", "acme-light.svg"), ("acme", "acme-dark.svg"), ("acme", "acme-mark.svg"), ("default", None)],
)
def test_the_web_head_serves_a_themes_logo_without_the_token_with_its_own_csp(acme, theme, file):
    client = web_client(acme("heinzel_acme"))
    listed = {t["name"]: t for t in client.get("/api/system/theme/list", headers=AUTH).json()["themes"]}
    name = file.removeprefix("acme-").removesuffix(".svg") if file else "mark"
    served = client.get(listed[theme]["logo"][name])
    assert served.status_code == 200, served.text
    if file:
        assert served.content == (LOGOS / file).read_bytes()
    assert served.content.startswith(b"<svg")
    assert {key: served.headers[key] for key in LOGO_HEADERS} == LOGO_HEADERS
    assert served.headers["cross-origin-resource-policy"] == "same-origin"
    assert client.get(listed[theme]["logo"][name], headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/theme-assets/acme/other.svg",
        "/theme-assets/acme/light.png",
        "/theme-assets/acme/light",
        "/theme-assets/acme/0.woff2",
        "/theme-assets/acme/logo%2Facme-light.svg",
        "/theme-assets/acme/..%2F..%2Fthemes%2Facme.toml",
        "/theme-assets/acme/light.svg/x",
        "/theme-assets/nope/light.svg",
        "/theme-assets/Acme/light.svg",
        "/theme-assets/..%2Facme/light.svg",
        "/theme-assets/acme-warm/light.svg",
    ],
)
def test_the_web_head_refuses_a_logo_no_theme_ships(acme, path):
    refused = web_client(acme("heinzel_acme")).get(path)
    assert refused.status_code == 404
    assert refused.headers["content-type"].startswith("application/json")
    assert "default-src 'none'; script-src 'self'" in refused.headers["content-security-policy"]


def test_only_the_theme_that_wins_its_name_serves_a_logo(acme):
    client = web_client(acme())  # the team's `acme` hides the company's and ships no logo
    assert client.get("/theme-assets/acme/light.svg").status_code == 404
    assert client.get("/theme-assets/default/light.svg").status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "attacker.example.test:8000"},
        {"Host": "127.0.0.1:9999"},
        {"Origin": "http://attacker.example.test"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ],
)
def test_logos_are_refused_to_another_host_or_site(acme, headers):
    refused = web_client(acme("heinzel_acme")).get("/theme-assets/acme/light.svg", headers=headers)
    assert refused.status_code == 403
    assert refused.content != (LOGOS / "acme-light.svg").read_bytes()


def test_no_logos_are_served_when_the_theme_module_is_off_or_cannot_be_built(acme, monkeypatch):
    kernel = acme("heinzel_acme")
    client = web_client(kernel)
    with monkeypatch.context() as patch:
        patch.setattr(kernel, "provides", lambda kind: False)
        assert client.get("/theme-assets/acme/light.svg").status_code == 404

    resolve = kernel.resolve

    def unbuildable(kind: type) -> object:
        if kind is ThemeFonts:
            msg = f"No module provides {kind.__name__}"
            raise HeinzelError(msg)
        return resolve(kind)

    monkeypatch.setattr(kernel, "resolve", unbuildable)
    assert client.get("/theme-assets/default/light.svg").status_code == 404


def test_a_logo_file_changed_after_import_is_left_out_and_not_served(acme, tmp_path, monkeypatch):
    copy = tmp_path / "layers"
    shutil.copytree(FIXTURES, copy)
    monkeypatch.syspath_prepend(str(copy))
    kernel = acme("heinzel_acme")
    logos = copy / "heinzel_acme" / "themes" / "logo"
    (logos / "acme-dark.svg").unlink()
    (logos / "acme-mark.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', "utf-8")
    client = web_client(kernel)
    logo = client.get("/api/system/theme/list", headers=AUTH).json()["themes"][1]["logo"]
    assert (logo["dark"], logo["mark"]) == (logo["light"], None)
    assert client.get("/theme-assets/acme/mark.svg").status_code == 404
    assert client.get("/theme-assets/acme/dark.svg").status_code == 404
    (logos / "acme-light.svg").unlink()
    assert client.get("/api/system/theme/list", headers=AUTH).json()["themes"][1]["logo"] is None
    assert client.get("/theme-assets/acme/light.svg").status_code == 404


def test_the_list_carries_each_themes_variants_with_their_backdrops(acme):
    _, listed = hz(acme("heinzel_acme"), "system", "theme", "list")
    themes = {view["name"]: view for view in listed["themes"]}  # type: ignore[index]
    assert themes["default"]["variant"] == []
    first, rain, night_rain = themes["acme"]["variant"]
    assert first["when"] == {"daypart": ["evening", "night"], "weather": []}
    assert rain["light"]["backdrop"] == {
        "gradient": "linear",
        "angle": 180,
        "stops": ["#cfd8dc", "background"],
        "opacity": 0.5,
    }
    assert night_rain["when"] == {"daypart": ["night"], "weather": ["rain"]}


def test_signals_carry_the_daypart_in_the_persons_zone_and_what_a_plugin_publishes(acme, monkeypatch):
    from heinzel.modules.system.theme import _service

    monkeypatch.setenv("HZ_ORG__TIMEZONE", "Europe/Lisbon")
    monkeypatch.setattr(_service, "utc_now", lambda: datetime(2026, 4, 1, 23, 0, tzinfo=UTC))
    code, signals = hz(acme("heinzel_acme"), "system", "theme", "signals")
    assert code == 0
    assert signals == {
        "daypart": "night",
        "weather": "rain",
        "sunrise": "2026-04-01T05:50:00+01:00",
        "sunset": "2026-04-01T18:40:00+01:00",
        "timezone": "Europe/Lisbon",
        "sources": ["acme"],
        "refresh": 60,
    }
    _, rendered = hz(acme("heinzel_acme"), "system", "theme", "signals", json_output=False)
    assert "Weather: rain" in str(rendered)
    assert "Sun: 05:50 to 18:40" in str(rendered)  # in Lisbon's time, not the publisher's UTC


def test_without_a_plugin_the_daypart_still_moves_and_the_weather_is_unknown(acme, monkeypatch):
    from heinzel.modules.system.theme import _service

    monkeypatch.setenv("HZ_ORG__TIMEZONE", "Europe/Berlin")
    monkeypatch.setattr(_service, "utc_now", lambda: datetime(2026, 6, 21, 10, 0, tzinfo=UTC))
    _, signals = hz(acme("heinzel_acme_lisbon"), "system", "theme", "signals")
    assert (signals["daypart"], signals["weather"], signals["sources"]) == ("day", None, [])  # type: ignore[index]


def weather_plugin(answers: list[object]) -> tuple[Module, list[int]]:
    """A plugin whose signals answer each of `answers` in turn, counting its calls."""
    module = Module("skies", "Made-up weather.")
    calls: list[int] = []

    @module.signals(every="10m")
    def now() -> ThemeSignals:
        """The made-up weather now."""
        calls.append(1)
        answer = answers[min(len(calls), len(answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer  # type: ignore[return-value]

    return module, calls


def test_a_plugins_signals_are_read_once_per_interval(acme, monkeypatch):
    from heinzel.kernel import ThemeFonts
    from heinzel.modules.system.theme import _service

    clock = [datetime(2026, 6, 21, 10, 0, tzinfo=UTC)]
    monkeypatch.setattr(_service, "utc_now", lambda: clock[0])
    module, calls = weather_plugin([ThemeSignals(weather="snow"), ThemeSignals(weather="fog")])
    kernel = acme("heinzel_acme_lisbon", extra=(module,))
    service = kernel.resolve(ThemeFonts)
    assert isinstance(service, _service.ThemeService)
    assert service.signals().weather == "snow"
    clock[0] += timedelta(minutes=9)
    assert (service.signals().weather, len(calls)) == ("snow", 1)
    clock[0] += timedelta(minutes=2)
    assert (service.signals().weather, len(calls)) == ("fog", 2)


@pytest.mark.parametrize("answer", [RuntimeError("offline"), {"weather": "sunny"}, "rain"])
def test_signals_that_fail_or_are_malformed_stay_unknown(acme, answer):
    from heinzel.kernel import ThemeFonts

    module, _ = weather_plugin([answer])
    service = acme("heinzel_acme_lisbon", extra=(module,)).resolve(ThemeFonts)
    signals = service.signals()  # type: ignore[attr-defined]
    assert (signals.weather, signals.sources) == (None, [])


def test_the_first_module_publishing_a_signal_wins(acme):
    from heinzel.kernel import ThemeFonts

    module, _ = weather_plugin([ThemeSignals(weather="snow")])
    signals = acme("heinzel_acme", extra=(module,)).resolve(ThemeFonts).signals()  # type: ignore[attr-defined]
    assert (signals.weather, signals.sources) == ("rain", ["acme"])  # skies' snow is shadowed: not a source


def test_the_web_head_serves_the_signals(acme):
    client = TestClient(
        web.build(acme("heinzel_acme"), port=8000, token=TOKEN, static=None), base_url="http://127.0.0.1:8000"
    )
    answer = client.get("/api/system/theme/signals", headers=AUTH)
    assert answer.status_code == 200
    assert answer.json()["weather"] == "rain"


def test_a_failing_plugin_is_not_called_again_within_its_interval(acme, monkeypatch):
    from heinzel.kernel import ThemeFonts
    from heinzel.modules.system.theme import _service

    clock = [datetime(2026, 6, 21, 10, 0, tzinfo=UTC)]
    monkeypatch.setattr(_service, "utc_now", lambda: clock[0])
    module, calls = weather_plugin([RuntimeError("offline"), ThemeSignals(weather="fog")])
    service = acme("heinzel_acme_lisbon", extra=(module,)).resolve(ThemeFonts)
    assert service.signals().weather is None  # type: ignore[attr-defined]
    clock[0] += timedelta(minutes=9)
    assert (service.signals().weather, len(calls)) == (None, 1)  # type: ignore[attr-defined]
    clock[0] += timedelta(minutes=2)
    assert (service.signals().weather, len(calls)) == ("fog", 2)  # type: ignore[attr-defined]


def test_a_hanging_plugin_leaves_its_signals_unknown_and_holds_up_nobody(acme, monkeypatch):
    import threading
    import time

    from heinzel.kernel import ThemeFonts
    from heinzel.modules.system.theme import _service

    monkeypatch.setattr(_service, "DEADLINE", timedelta(milliseconds=50))
    release = threading.Event()
    module = Module("stuck", "Made-up weather that never answers.")
    calls: list[int] = []

    @module.signals(every="10m")
    def now() -> ThemeSignals:
        """Hangs."""
        calls.append(1)
        release.wait(5)
        return ThemeSignals(weather="clear")

    service = acme("heinzel_acme_lisbon", extra=(module,)).resolve(ThemeFonts)
    started = time.monotonic()
    answers = []
    threads = [threading.Thread(target=lambda: answers.append(service.signals().weather)) for _ in range(4)]  # type: ignore[attr-defined]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
    release.set()
    assert answers == [None] * 4
    assert time.monotonic() - started < 4  # the 5 s hang held up nobody
    assert len(calls) == 1  # one call in flight; the others got the last values, unknown


def test_theme_avatar_decorations_reach_cli_and_web_json(acme):
    winter = Module("winter", "A winter look.")
    winter.theme(
        Theme.model_validate(
            DEFAULT_THEME.model_dump()
            | {
                "name": "winter",
                "title": "Winter",
                "heinzel": {"palette": {"hat": "#def"}, "decorations": ["snow"]},
            }
        )
    )
    kernel = acme(extra=(winter,))
    code, listed = hz(kernel, "system", "theme", "list")
    assert code == 0
    look = next(theme for theme in listed["themes"] if theme["name"] == "winter")["heinzel"]
    assert look == {"palette": {"hat": "#def"}, "decorations": ["snow"], "assets": {}}
    client = TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url="http://127.0.0.1:8000")
    response = client.get("/api/system/theme/list", headers=AUTH)
    assert response.status_code == 200
    assert next(theme for theme in response.json()["themes"] if theme["name"] == "winter")["heinzel"] == look


def test_original_avatar_layers_have_owned_urls_and_safe_fallback(acme, tmp_path):
    file = tmp_path / "hat.svg"
    file.write_text('<svg xmlns="http://www.w3.org/2000/svg"><circle r="8"/></svg>')
    theme_file = tmp_path / "winter.toml"
    colours = "\n".join(
        f"[{mode}]\n"
        + "\n".join(f"{key} = {json.dumps(value)}" for key, value in values.model_dump(exclude_none=True).items())
        for mode, values in (("light", DEFAULT_THEME.light), ("dark", DEFAULT_THEME.dark))
    )
    theme_file.write_text(
        'name = "winter"\ntitle = "Winter"\nfont_sans = "system-ui, sans-serif"\n'
        'font_mono = "monospace"\nborder_width = "1px"\n[heinzel.assets]\nhat-grown = "hat.svg"\n' + colours,
        encoding="utf-8",
    )
    winter = Module("winter", "Winter artwork.")
    winter.theme(theme_file)
    kernel = acme(extra=(winter,))
    client = web_client(kernel)
    _, listing = hz(kernel, "system", "theme", "list")
    listed = next(value for value in listing["themes"] if value["name"] == "winter")
    url = listed["heinzel_assets"]["hat-grown"]
    assert listed["heinzel"]["assets"] == {}
    assert client.get("/api/system/theme/list", headers=AUTH).json()["themes"][-1]["heinzel_assets"] == {
        "hat-grown": url
    }
    image = client.get(url)
    assert image.status_code == 200
    assert image.content == file.read_bytes()
    assert client.get(url.split("?")[0]).status_code == 404
    assert client.get(url[:-16] + "0" * 16).status_code == 404
    file.write_text('<svg xmlns="http://www.w3.org/2000/svg"><circle r="9"/></svg>')
    assert client.get(url).status_code == 404
    current = client.get("/api/system/theme/list", headers=AUTH).json()
    changed = next(value for value in current["themes"] if value["name"] == "winter")["heinzel_assets"]["hat-grown"]
    assert changed != url
    assert client.get(changed).status_code == 200
    assert image.headers["content-security-policy"] == "default-src 'none'; style-src 'unsafe-inline'"
    assert client.get(url.replace("hat-grown", "hat-baby")).status_code == 404
    assert client.get(url.replace("hat-grown", "outfit-old")).status_code == 404
    file.write_text('<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.invalid/a"/></svg>')
    assert client.get(url).status_code == 404
    listed = client.get("/api/system/theme/list", headers=AUTH).json()
    assert next(value for value in listed["themes"] if value["name"] == "winter")["heinzel_assets"] == {}
