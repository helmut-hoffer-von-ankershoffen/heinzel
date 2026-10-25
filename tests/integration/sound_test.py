"""Soundscapes end to end (ADR 0058): plugins offer them, the CLI and the web head list them, the pick round-trips,
the web head serves their audio, and the page admits the stream's one frame origin only while a stream is in effect.

`tests/fixtures/sound/heinzel_sound_demo` is a real plugin with a tiny CC0 layered soundscape (`demo`, paired with the
Acme theme); the Acme company layer in `tests/fixtures/layers` brings that theme. Nothing reaches the network.
"""

import importlib
import json
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from heinzel import Config, HeinzelError, Kernel, Module, ModuleRecord, Settings, Soundscape, ThemeChoice
from heinzel.heads import web
from heinzel.heads.cli import build
from heinzel.kernel import SOUND_CSP, Origin, discover, user_config_dir, walk
from heinzel.modules.system.sound import SoundService

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parents[1] / "fixtures"
PLUGINS = ("heinzel_acme", "heinzel_sound_demo")
ACME = ("heinzel_acme", "heinzel_acme_payments", "heinzel_acme_lisbon", "heinzel_acme_payments_checkout")
TOKEN = "s3ssion-token-for-tests"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
FRAMED = f"{web.CSP}; frame-src https://www.youtube-nocookie.com"
STREAM = {"kind": "stream", "stream": {"provider": "youtube", "video": "tRsQsTMvPNg"}}


@pytest.fixture
def kernel(monkeypatch, settings: Settings):
    """Builds a kernel with the built-in modules, the plugins asked for (Acme and the demo by default) and `extra`."""
    monkeypatch.syspath_prepend(str(FIXTURES / "layers"))
    monkeypatch.syspath_prepend(str(FIXTURES / "sound"))

    def make(*plugins: str, extra: tuple[Module, ...] = ()) -> Kernel:
        records = discover(plugins=False)[0]
        for name in plugins or PLUGINS:
            origin = Origin(distribution=name.replace("_", "-"), version="1.0.0", builtin=False)
            records += walk(importlib.import_module(name), origin)
        test = Origin(distribution="heinzel-test", version="1.0.0", builtin=False)
        records += [ModuleRecord(qualname=module.name, module=module, origin=test) for module in extra]
        return Kernel(settings, records, config=Config(files=(), data={}))

    yield make
    for name in {*PLUGINS, *ACME}:  # imported afresh by the next test, so no test sees another's modules
        sys.modules.pop(name, None)


def hz(kernel: Kernel, *args: str, json_output: bool = True) -> tuple[int, object]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"] if json_output else list(args))
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, result.output


def configure(text: str) -> None:
    user_config_dir().mkdir(parents=True, exist_ok=True)
    (user_config_dir() / "config.toml").write_text(text, encoding="utf-8")


def paired(name: str, module_name: str | None = None) -> Module:
    """A plugin outside every layer that offers a stream soundscape paired with the Acme theme."""
    module = Module(module_name or name, "Pairs with Acme.")
    module.soundscape(Soundscape.model_validate({"name": name, "title": name.title(), "pairs_with": ["acme"]} | STREAM))
    return module


@pytest.fixture
def static(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>hz</title>", encoding="utf-8")
    (tmp_path / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    return tmp_path


def client(kernel: Kernel, static: Path | None = None) -> TestClient:
    return TestClient(web.build(kernel, port=8000, token=TOKEN, static=static), base_url="http://127.0.0.1:8000")


def test_sound_is_off_by_default_and_the_list_shows_off_the_core_streams_and_each_plugins_soundscape(kernel):
    code, listed = hz(kernel("heinzel_sound_demo"), "system", "sound", "list")
    assert code == 0, listed
    assert (listed["selected"], listed["effective"]) == ("off", "off")
    off, fm, focus, demo = listed["soundscapes"]
    assert (off["name"], off["kind"], off["selected"], off["effective"]) == ("off", "off", True, True)
    assert (fm["name"], fm["title"], fm["module"], fm["plugin"]) == (
        "claude-fm",
        "Claude FM",
        "system.sound",
        "heinzel",
    )
    assert fm["stream"] == {
        "provider": "youtube",
        "video": "tRsQsTMvPNg",
        "embed_url": "https://www.youtube-nocookie.com/embed/tRsQsTMvPNg",
        "frame_origin": "https://www.youtube-nocookie.com",
        "watch_url": "https://www.youtube.com/watch?v=tRsQsTMvPNg",
    }
    assert (focus["name"], focus["title"], focus["plugin"]) == ("focus", "Focus", "heinzel")
    assert focus["stream"]["video"] == "5Le8Mrx3uA0"
    assert (demo["name"], demo["kind"], demo["plugin"], demo["pairs_with"]) == (
        "demo",
        "layers",
        "heinzel-sound-demo",
        ["acme"],
    )
    (layer,) = demo["layers"]
    assert layer["url"].startswith("/sound-assets/demo/layer-0.opus?v=")
    assert (layer["licence"], layer["signal"], layer["gain"]) == ("CC0-1.0", "daypart", 0.6)
    assert layer["curve"] == {"morning": 0.8, "day": 1.0, "evening": 0.5, "night": 0.2}
    assert [(e["kind"], e["licence"]) for e in demo["earcons"]] == [("draft", "CC0-1.0")]
    assert listed["signals"]["daypart"] in {"dawn", "morning", "day", "dusk", "evening", "night"}
    assert "weather.rain" not in listed["signals"]  # absent until a plugin publishes it
    assert (listed["volume"], listed["follow_theme"], listed["mute_in_meetings"]) == (50, False, True)
    assert listed["next"] == ["hz system sound use claude-fm"]


def test_sound_use_writes_the_user_setting_refuses_an_unknown_name_and_off_switches_it_off(kernel):
    hzk = kernel("heinzel_sound_demo")
    code, used = hz(hzk, "system", "sound", "use", "claude-fm")
    assert code == 0, used
    assert (used["selected"], used["effective"]) == ("claude-fm", "claude-fm")
    assert '[system.sound]\nname = "claude-fm"' in (user_config_dir() / "config.toml").read_text(encoding="utf-8")
    code, output = hz(hzk, "system", "sound", "use", "nope", json_output=False)
    assert code != 0
    assert "No soundscape is called 'nope'; available: off, claude-fm, focus, demo" in " ".join(str(output).split())
    assert hz(hzk, "system", "sound", "use", "off")[1]["effective"] == "off"


def test_a_soundscape_whose_plugin_is_gone_or_an_invalid_value_falls_back_to_off_and_says_so(kernel):
    configure('[system.sound]\nname = "rain"\n')
    listed = hz(kernel(), "system", "sound", "list")[1]
    assert listed["effective"] == "off"
    assert "No installed plugin offers the soundscape 'rain'" in listed["note"]
    assert listed["next"] == ["hz system sound use off"]
    configure("[system.sound]\nvolume = 400\n")
    listed = hz(kernel(), "system", "sound", "list")[1]
    assert (listed["effective"], listed["volume"]) == ("off", 50)
    assert "isn't valid" in listed["note"]


def test_a_project_file_cannot_pick_a_soundscape_or_its_settings(kernel, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hz.toml").write_text(
        '[system.sound]\nname = "claude-fm"\nvolume = 100\nfollow_theme = true\nmute_in_meetings = false\n',
        encoding="utf-8",
    )
    listed = hz(kernel(), "system", "sound", "list")[1]
    assert (listed["effective"], listed["volume"], listed["follow_theme"], listed["mute_in_meetings"]) == (
        "off",
        50,
        False,
        True,
    )


def test_follow_my_theme_plays_the_soundscape_that_pairs_with_the_theme_in_effect(kernel):
    hzk = kernel()
    hz(hzk, "system", "sound", "use", "claude-fm")
    assert hz(hzk, "system", "sound", "list")[1]["effective"] == "claude-fm"  # follow_theme is off
    configure('[system.sound]\nname = "claude-fm"\nfollow_theme = true\n')
    assert hz(hzk, "system", "sound", "list")[1]["effective"] == "claude-fm"  # the default theme pairs with none
    hz(hzk, "system", "theme", "use", "acme")
    listed = hz(hzk, "system", "sound", "list")[1]
    assert (listed["selected"], listed["effective"]) == ("claude-fm", "demo")
    assert "The theme 'acme' brings its own soundscape, 'demo'" in listed["note"]
    assert [s["name"] for s in listed["soundscapes"] if s["effective"]] == ["demo"]


def test_of_several_soundscapes_paired_with_one_theme_in_one_layer_the_first_by_name_wins(kernel):
    # The modules' names sort the other way round: the soundscape's name decides, not the module's.
    hzk = kernel("heinzel_acme", extra=(paired("zulu", "aaa"), paired("alpha", "zzz")))
    configure('[system.theme]\nname = "acme"\n[system.sound]\nfollow_theme = true\n')
    assert hz(hzk, "system", "sound", "list")[1]["effective"] == "alpha"


def test_the_more_specific_layer_wins_a_soundscape_name_and_an_org_and_site_tie_is_left_out(kernel):
    code, listed = hz(kernel(*ACME), "system", "sound", "list")
    assert code == 0, listed
    (radio,) = [s for s in listed["soundscapes"] if s["name"] == "acme-radio"]
    assert (radio["title"], radio["module"], radio["shadows"]) == ("Checkout radio", "acme-payments-checkout", ["acme"])
    assert "acme-warm-radio" not in [s["name"] for s in listed["soundscapes"]]
    (conflict,) = listed["conflicts"]
    assert conflict.startswith("acme-warm-radio: offered by acme-lisbon and acme-payments, an org and the site")
    assert hz(kernel(*ACME), "system", "sound", "use", "acme-warm-radio")[0] != 0
    output = hz(kernel(*ACME), "system", "sound", "list", json_output=False)[1]
    assert "Left out: acme-warm-radio" in " ".join(str(output).split())


def test_a_layer_whose_file_is_gone_after_import_drops_out_of_the_list_and_is_not_served(kernel, tmp_path, monkeypatch):
    copy = tmp_path / "sound"
    shutil.copytree(FIXTURES / "sound", copy)
    monkeypatch.syspath_prepend(str(copy))
    sys.modules.pop("heinzel_sound_demo", None)
    hzk = kernel("heinzel_sound_demo")
    (copy / "heinzel_sound_demo" / "soundscapes" / "sounds" / "hum.opus").unlink()
    web_client = client(hzk)
    listed = web_client.get("/api/system/sound/list", headers=AUTH)
    assert listed.status_code == 200, listed.text
    (demo,) = [s for s in listed.json()["soundscapes"] if s["name"] == "demo"]
    assert demo["layers"] == []
    assert [e["kind"] for e in demo["earcons"]] == ["draft"]
    assert web_client.get("/sound-assets/demo/layer-0.opus").status_code == 404


@pytest.mark.parametrize("how", ["module off", "service fails"])
def test_follow_my_theme_without_the_theme_module_plays_the_pick(kernel, monkeypatch, how):
    hzk = kernel()
    configure('[system.theme]\nname = "acme"\n[system.sound]\nname = "claude-fm"\nfollow_theme = true\n')
    provides, resolve = hzk.provides, hzk.resolve
    if how == "module off":
        monkeypatch.setattr(hzk, "provides", lambda kind: kind is not ThemeChoice and provides(kind))
    else:

        def unbuildable(kind: type) -> object:
            if kind is ThemeChoice:
                msg = "No module provides ThemeChoice"
                raise HeinzelError(msg)
            return resolve(kind)

        monkeypatch.setattr(hzk, "resolve", unbuildable)
    listed = hz(hzk, "system", "sound", "list")[1]
    assert (listed["selected"], listed["effective"], listed["note"]) == ("claude-fm", "claude-fm", None)


def test_audio_answers_404_when_reading_it_fails(kernel, monkeypatch):
    def broken(*_: object) -> None:
        msg = "system.sound can't be built"
        raise HeinzelError(msg)

    monkeypatch.setattr(SoundService, "asset", broken)
    refused = client(kernel()).get("/sound-assets/demo/layer-0.opus")
    assert (refused.status_code, refused.headers["content-security-policy"]) == (404, web.CSP)


def test_the_cli_renders_the_soundscapes_with_the_one_in_effect_marked(kernel):
    code, output = hz(kernel("heinzel_sound_demo"), "system", "sound", "list", json_output=False)
    assert code == 0, output
    assert "*  off" in str(output)
    assert "claude-fm   Claude FM  stream (youtube)" in str(output)
    assert "demo        Demo hum   1 layer " in str(output)


def test_the_web_head_lists_soundscapes_and_the_pick_round_trips_behind_the_token(kernel):
    web_client = client(kernel())
    assert web_client.get("/api/system/sound/list").status_code == 401
    assert web_client.get("/api/system/sound/list", headers=AUTH).json()["effective"] == "off"
    used = web_client.post("/api/system/sound/use", params={"name": "claude-fm"}, headers=AUTH)
    assert used.status_code == 200, used.text
    assert web_client.get("/api/system/sound/list", headers=AUTH).json()["effective"] == "claude-fm"


def test_the_web_head_serves_a_soundscapes_audio_typed_by_its_signature_without_the_token(kernel):
    web_client = client(kernel())
    listed = web_client.get("/api/system/sound/list", headers=AUTH).json()["soundscapes"]
    (demo,) = [s for s in listed if s["name"] == "demo"]
    for url in (demo["layers"][0]["url"], demo["earcons"][0]["url"]):
        served = web_client.get(url, headers={"Sec-Fetch-Site": "same-origin"})
        assert served.status_code == 200, served.text
        assert served.content.startswith(b"OggS")
        assert served.headers["content-type"] == "audio/ogg"
        assert served.headers["content-security-policy"] == SOUND_CSP
        assert served.headers["x-content-type-options"] == "nosniff"
        assert served.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.parametrize(
    "path",
    [
        "/sound-assets/demo/layer-0.ogg",  # the file is .opus: the extension must be the declared one's
        "/sound-assets/demo/layer-0.mp3",
        "/sound-assets/demo/layer-1.opus",
        "/sound-assets/demo/earcon-failed.opus",
        "/sound-assets/demo/hum.opus",
        "/sound-assets/demo/..%2Fdemo.toml",
        "/sound-assets/claude-fm/layer-0.opus",
        "/sound-assets/nope/layer-0.opus",
        "/sound-assets/Demo/layer-0.opus",
    ],
)
def test_the_web_head_refuses_a_file_no_soundscape_ships(kernel, path):
    refused = client(kernel()).get(path)
    assert refused.status_code == 404
    assert refused.headers["content-security-policy"] == web.CSP


@pytest.mark.parametrize(
    "headers", [{"Host": "evil.example"}, {"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}]
)
def test_audio_is_refused_to_another_host_or_site(kernel, headers):
    assert client(kernel()).get("/sound-assets/demo/layer-0.opus", headers=headers).status_code == 403


def test_the_page_admits_the_streams_frame_origin_only_while_a_stream_is_in_effect(kernel, static):
    web_client = client(kernel(), static)
    assert web_client.get("/").headers["content-security-policy"] == web.CSP  # off: no frame at all
    hz(kernel(), "system", "sound", "use", "claude-fm")
    page = web_client.get("/contracts")
    assert page.headers["content-security-policy"] == FRAMED
    assert page.headers["x-frame-options"] == "DENY"
    # Nothing but the page: the API, the app's files and the audio keep the strict policy.
    assert web_client.get("/api/system/sound/list", headers=AUTH).headers["content-security-policy"] == web.CSP
    assert web_client.get("/favicon.svg").headers["content-security-policy"] == web.CSP
    hz(kernel(), "system", "sound", "use", "demo")
    assert web_client.get("/").headers["content-security-policy"] == web.CSP  # layered: no frame
    configure('[system.theme]\nname = "acme"\n[system.sound]\nname = "demo"\nfollow_theme = true\n')
    assert web_client.get("/").headers["content-security-policy"] == web.CSP


def test_follow_my_theme_to_a_stream_admits_its_frame_origin(kernel, static):
    hzk = kernel("heinzel_acme", extra=(paired("radio"),))
    configure('[system.theme]\nname = "acme"\n[system.sound]\nfollow_theme = true\n')
    assert client(hzk, static).get("/").headers["content-security-policy"] == FRAMED


def test_the_page_admits_no_frame_when_the_sound_service_fails(kernel, static, monkeypatch):
    configure('[system.sound]\nname = "claude-fm"\n')

    def broken(_: SoundService) -> str | None:
        raise RuntimeError

    monkeypatch.setattr(SoundService, "stream_provider", broken)
    assert client(kernel(), static).get("/").headers["content-security-policy"] == web.CSP


def test_the_page_admits_no_frame_for_a_provider_the_head_does_not_know(kernel, static, monkeypatch):
    monkeypatch.setattr(SoundService, "stream_provider", lambda _: "evil.example")
    assert client(kernel(), static).get("/").headers["content-security-policy"] == web.CSP


def test_no_audio_and_no_frame_when_the_sound_module_is_off(kernel, static, monkeypatch):
    configure('[system.sound]\nname = "claude-fm"\n')
    hzk = kernel()
    web_client = client(hzk, static)
    monkeypatch.setattr(hzk, "provides", lambda kind: False)
    assert web_client.get("/").headers["content-security-policy"] == web.CSP
    assert web_client.get("/sound-assets/demo/layer-0.opus").status_code == 404
