"""Soundscapes as data: every field and every audio file is checked before the web app can play it (ADR 0058)."""

import os
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from heinzel import Module, SoundAssets, SoundLayer, Soundscape, SoundscapeLet, SoundStream, ThemeChoice
from heinzel.kernel import SIGNALS, SOUND_CSP, SOUND_ROUTE, STREAM_PROVIDERS
from heinzel.kernel._soundscapes import (
    MAX_ASSET_BYTES,
    MAX_SOUNDSCAPE_BYTES,
    audio_type,
    read_audio,
    sourced_soundscapes,
)

pytestmark = pytest.mark.unit

OGG = b"OggS" + b"\0" * 60
# Two MPEG-1 Layer III frames at 128 kbit/s and 44.1 kHz: 417 bytes each, the second header right after the first.
FRAME = b"\xff\xfb\x90\x00" + b"\0" * 413
MP3 = FRAME * 2
STREAM = {"name": "fm", "title": "FM", "kind": "stream", "stream": {"provider": "youtube", "video": "tRsQsTMvPNg"}}


def layers(**overrides: object) -> dict[str, object]:
    layer = {"file": "sounds/rain.ogg", "licence": "CC0-1.0"}
    return {"name": "rain", "title": "Rain", "kind": "layers", "layers": [layer]} | overrides


def write(directory: Path, name: str = "sounds/rain.ogg", content: bytes = OGG) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def let(data: dict[str, object], directory: Path | None) -> SoundscapeLet:
    soundscape = Soundscape.model_validate(data)
    return SoundscapeLet(name=soundscape.name, soundscape=soundscape, directory=directory)


def test_a_stream_names_an_allowed_provider_and_a_video_never_a_url():
    stream = Soundscape.model_validate(STREAM).stream
    assert stream == SoundStream(provider="youtube", video="tRsQsTMvPNg")
    assert STREAM_PROVIDERS["youtube"].frame_origin == "https://www.youtube-nocookie.com"
    assert STREAM_PROVIDERS["youtube"].additional_frame_origins == ("https://www.youtube.com",)
    assert STREAM_PROVIDERS["youtube"].embed_url("tRsQsTMvPNg") == "https://www.youtube-nocookie.com/embed/tRsQsTMvPNg"
    assert STREAM_PROVIDERS["youtube"].watch_url("x") == "https://www.youtube.com/watch?v=x"


@pytest.mark.parametrize(
    "stream",
    [
        {"provider": "vimeo", "video": "tRsQsTMvPNg"},
        {"provider": "youtube", "video": "short"},
        {"provider": "youtube", "video": "tRsQsTMvPN/"},
        {"provider": "youtube", "video": "tRsQsTMvPNg", "url": "https://evil.example/"},
        {"provider": "youtube", "video": "tRsQsTMvPNg", "frame_origin": "https://evil.example"},
    ],
)
def test_a_stream_with_another_host_a_bad_id_or_a_url_is_refused(stream):
    with pytest.raises(ValidationError):
        Soundscape.model_validate(STREAM | {"stream": stream})


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        (STREAM | {"layers": [{"file": "a.ogg", "licence": "CC0-1.0"}]}, "no [[layers]]"),
        ({k: v for k, v in STREAM.items() if k != "stream"}, "has a [stream]"),
        (layers(layers=[]), "1 to 8 [[layers]]"),
        (layers(stream=STREAM["stream"]), "no [stream]"),
        (layers(kind="mixtape"), "kind"),
        (layers(name="Rain"), "name"),
        (layers(title="two\nlines"), "title"),
        (layers(fade_seconds=0), "fade_seconds"),
        (layers(fade_seconds=4.5), "fade_seconds"),
        (layers(volume=1), "volume"),
        (layers(pairs_with=["Not A Theme"]), "pairs_with"),
        (layers(layers=[{"file": "a.ogg", "licence": "CC0-1.0"}] * 9), "layers"),
    ],
)
def test_a_malformed_soundscape_is_refused_with_its_field(data, problem):
    with pytest.raises(ValidationError, match=re.escape(problem)):
        Soundscape.model_validate(data)


@pytest.mark.parametrize("file", ["../rain.ogg", "/etc/rain.ogg", "sounds\\rain.ogg", ".hidden.ogg", "rain.wav"])
def test_an_audio_path_must_be_plain_names_inside_the_directory_ending_in_an_audio_extension(file):
    with pytest.raises(ValidationError, match="file"):
        SoundLayer.model_validate({"file": file, "licence": "CC0-1.0"})


@pytest.mark.parametrize("licence", ["CC-BY-SA-4.0", "CC-BY-NC-4.0", "MIT", "proprietary", "cc0-1.0"])
def test_only_public_domain_and_attribution_licences_are_allowed(licence):
    with pytest.raises(ValidationError, match="licence"):
        SoundLayer.model_validate({"file": "a.ogg", "licence": licence})


def test_an_attribution_licence_needs_an_attribution_and_a_source_is_https_without_credentials():
    with pytest.raises(ValidationError, match="needs an attribution"):
        SoundLayer.model_validate({"file": "a.ogg", "licence": "CC-BY-4.0"})
    layer = SoundLayer.model_validate(
        {"file": "a.ogg", "licence": "CC-BY-4.0", "attribution": "Jo Example", "source": "https://example.org/s/1"}
    )
    assert layer.attribution == "Jo Example"
    for source in ("http://example.org/", "https://user:pw@example.org/", "javascript:alert(1)", "https://"):
        with pytest.raises(ValidationError, match="https URL"):
            SoundLayer.model_validate({"file": "a.ogg", "licence": "CC0-1.0", "source": source})
    with pytest.raises(ValidationError, match="printable"):
        SoundLayer.model_validate({"file": "a.ogg", "licence": "CC-BY-4.0", "attribution": "a\nb"})


@pytest.mark.parametrize(
    ("signal", "curve", "ok"),
    [
        ("none", None, True),
        ("none", [[0, 0], [1, 1]], False),
        ("weather.rain", None, False),
        ("weather.rain", [[0, 0], [1, 1]], True),
        ("weather.wind", [[0, 0.2], [0.5, 0.8], [1, 1]], True),
        ("weather.rain", [[0, 0]], False),
        ("weather.rain", [[0.5, 0], [0.2, 1]], False),
        ("weather.rain", [[0, 0], [0, 1]], False),
        ("weather.rain", [[0, 0], [1, 1.5]], False),
        ("weather.rain", [[i / 9, 0] for i in range(9)], False),
        ("weather.rain", {"morning": 1}, False),
        ("daypart", {"morning": 1, "evening": 0.3}, True),
        ("daypart", {"brunch": 1}, False),
        ("daypart", {"morning": 2}, False),
        ("daypart", [[0, 0], [1, 1]], False),
        ("meeting", None, False),
    ],
)
def test_a_layer_follows_a_signal_of_the_shared_vocabulary_through_a_matching_curve(signal, curve, ok):
    data = {"file": "a.ogg", "licence": "CC0-1.0", "signal": signal, "curve": curve}
    if ok:
        assert SoundLayer.model_validate(data).signal == signal
    else:
        with pytest.raises(ValidationError):
            SoundLayer.model_validate(data)


def test_the_signals_are_named_and_a_daypart_curve_takes_the_dayparts_of_themes():
    assert SIGNALS == ("daypart", "weather.rain", "weather.wind")
    data = {"file": "a.ogg", "licence": "CC0-1.0", "signal": "daypart", "curve": {"dusk": 1.0, "dawn": 0.5}}
    assert SoundLayer.model_validate(data).signal == "daypart"
    with pytest.raises(ValidationError):
        SoundLayer.model_validate({**data, "curve": {"afternoon": 1.0}})


def test_earcons_are_keyed_by_notice_kind_and_licensed_like_layers():
    data = layers(earcons={"draft": {"file": "chime.ogg", "licence": "CC0-1.0"}})
    assert list(Soundscape.model_validate(data).assets()) == ["layer-0", "earcon-draft"]
    with pytest.raises(ValidationError):
        Soundscape.model_validate(layers(earcons={"party": {"file": "chime.ogg", "licence": "CC0-1.0"}}))
    with pytest.raises(ValidationError):
        Soundscape.model_validate(layers(earcons={"draft": {"file": "chime.ogg"}}))


@pytest.mark.parametrize(
    ("content", "extension", "media_type"),
    [
        (OGG, "ogg", "audio/ogg"),
        (OGG, "opus", "audio/ogg"),
        (b"ID3\x04" + b"\0" * 20, "mp3", "audio/mpeg"),
        (MP3, "mp3", "audio/mpeg"),
        (FRAME, "mp3", None),  # one frame sync alone is too weak a signature
        (b"\xff\xfb" + b"\0" * 900, "mp3", None),
        (MP3, "ogg", None),
        (OGG, "mp3", None),
        (b"RIFF....WAVE", "ogg", None),
        (b"<html>", "mp3", None),
        (OGG, "wav", None),
    ],
)
def test_the_type_of_an_audio_file_comes_from_its_signature_and_must_match_its_extension(
    content, extension, media_type
):
    assert audio_type(content, extension) == media_type


def test_a_soundscape_file_is_read_with_its_directory_and_its_files_checked(tmp_path):
    write(tmp_path)
    (tmp_path / "rain.toml").write_text(
        'name = "rain"\ntitle = "Rain"\nkind = "layers"\n[[layers]]\nfile = "sounds/rain.ogg"\nlicence = "CC0-1.0"\n',
        encoding="utf-8",
    )
    ((soundscape, directory),) = sourced_soundscapes(tmp_path)
    assert (soundscape.name, directory) == ("rain", tmp_path)
    offered = SoundscapeLet(name="rain", soundscape=soundscape, directory=directory)
    assert offered.asset("layer-0") == (OGG, "audio/ogg", "ogg")
    assert re.fullmatch(rf"{SOUND_ROUTE}/rain/layer-0\.ogg\?v=[0-9a-f]{{16}}", offered.asset_url("layer-0"))
    with pytest.raises(ValueError, match="has no 'layer-1'"):
        offered.asset("layer-1")


def test_soundscape_files_that_are_not_valid_name_the_file_and_the_rule(tmp_path):
    (tmp_path / "bad.toml").write_text('name = "rain"\ntitle = "Rain"\nkind = "stream"\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"bad\.toml isn't a valid soundscape: soundscape: .*\[stream\]"):
        sourced_soundscapes(tmp_path)
    (tmp_path / "bad.toml").write_text("name = ", encoding="utf-8")
    with pytest.raises(ValueError, match="isn't valid TOML"):
        sourced_soundscapes(tmp_path)
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="holds no soundscape files"):
        sourced_soundscapes(tmp_path / "empty")
    with pytest.raises(ValueError, match=r"must end in \.toml"):
        sourced_soundscapes(tmp_path / "a.txt")


def test_an_audio_file_that_is_missing_too_large_or_not_audio_is_refused_at_import(tmp_path):
    with pytest.raises(ValueError, match="doesn't exist"):
        let(layers(), tmp_path)
    write(tmp_path, content=b"<html>not audio</html>")
    with pytest.raises(ValueError, match="isn't the audio its extension says"):
        let(layers(), tmp_path)
    write(tmp_path, content=OGG + b"\0" * MAX_ASSET_BYTES)
    with pytest.raises(ValueError, match="larger than 2 MB"):
        let(layers(), tmp_path)
    (tmp_path / "sounds" / "rain.ogg").unlink()
    (tmp_path / "sounds" / "rain.ogg").mkdir()
    with pytest.raises(ValueError, match="isn't a file"):
        let(layers(), tmp_path)


def test_all_files_of_one_soundscape_are_at_most_8_mb(tmp_path):
    big = OGG + b"\0" * (MAX_ASSET_BYTES - len(OGG))
    files = [{"file": f"sounds/{index}.ogg", "licence": "CC0-1.0"} for index in range(5)]
    for index in range(5):
        write(tmp_path, f"sounds/{index}.ogg", big)
    assert 5 * len(big) > MAX_SOUNDSCAPE_BYTES
    with pytest.raises(ValueError, match="more than 8 MB"):
        let(layers(layers=files), tmp_path)


def test_a_symlink_out_of_the_soundscape_directory_is_refused_one_inside_is_followed(tmp_path):
    directory, outside = tmp_path / "plugin", tmp_path / "outside.ogg"
    outside.write_bytes(OGG)
    (directory / "sounds").mkdir(parents=True)
    (directory / "sounds" / "rain.ogg").symlink_to(outside)
    with pytest.raises(ValueError, match="leads out of the soundscape's directory"):
        let(layers(), directory)
    (directory / "sounds" / "rain.ogg").unlink()
    write(directory, "sounds/real.ogg")
    (directory / "sounds" / "rain.ogg").symlink_to(directory / "sounds" / "real.ogg")
    assert let(layers(), directory).asset("layer-0")[1] == "audio/ogg"


def test_a_file_swapped_after_import_is_refused_when_read(tmp_path):
    path = write(tmp_path)
    offered = let(layers(), tmp_path)
    path.write_bytes(b"<script>alert(1)</script>")
    with pytest.raises(ValueError, match="isn't the audio its extension says"):
        offered.asset("layer-0")
    path.unlink()
    os.mkfifo(path)  # a FIFO swapped in must not block the server
    with pytest.raises(ValueError, match="isn't a file"):
        offered.asset("layer-0")


def test_read_audio_checks_the_path_rule_itself():
    layer = SoundLayer.model_construct(file="../escape.ogg", licence="CC0-1.0")
    with pytest.raises(ValueError, match="isn't a path inside"):
        read_audio(Path(), layer)


def test_the_let_checks_again_refuses_off_a_wrong_name_and_files_without_a_directory(tmp_path):
    with pytest.raises(ValueError, match="no sound"):
        let(STREAM | {"name": "off"}, None)
    with pytest.raises(ValueError, match="named after its soundscape"):
        SoundscapeLet(name="other", soundscape=Soundscape.model_validate(STREAM))
    with pytest.raises(ValueError, match="comes from a soundscape file"):
        let(layers(), None)
    stream = SoundStream.model_construct(provider="evil", video="x")
    sneaky = Soundscape.model_construct(**(STREAM | {"stream": stream}))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="isn't valid"):
        SoundscapeLet(name="fm", soundscape=sneaky)


def test_module_soundscape_offers_each_file_and_refuses_a_second_of_one_name(tmp_path):
    write(tmp_path)
    (tmp_path / "fm.toml").write_text(
        'name = "fm"\ntitle = "FM"\nkind = "stream"\n[stream]\nprovider = "youtube"\nvideo = "tRsQsTMvPNg"\n',
        encoding="utf-8",
    )
    module = Module("example", "Example.")
    (offered,) = module.soundscape(tmp_path)
    assert offered.name == "fm"
    with pytest.raises(ValueError, match="already has a SoundscapeLet named 'fm'"):
        module.soundscape(Soundscape.model_validate(STREAM))


def test_the_interfaces_are_abstract_and_the_audio_csp_loads_nothing():
    with pytest.raises(NotImplementedError):
        SoundAssets().asset("demo", "layer-0", "ogg")
    with pytest.raises(NotImplementedError):
        SoundAssets().stream_provider()
    with pytest.raises(NotImplementedError):
        ThemeChoice().selected()
    assert SOUND_CSP == "default-src 'none'; sandbox"
