"""Soundscapes: background sound for the web app, as data a plugin ships, chosen like a theme (ADR 0058).

A soundscape is either layered audio, licensed files in the plugin that the web app mixes live (each layer's gain bound
to a signal by a curve), or a stream in the official player of a host `STREAM_PROVIDERS` allows. No URL or host is
data: a stream names a provider and an id, and the frame origin comes from the table here. Audio files are checked at
import and again when the web head serves them, like a theme's fonts: inside the soundscape's directory, a regular
file, within the size limits, and of the type its signature says.
"""

import hashlib
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator

from heinzel.kernel._lets import Let
from heinzel.kernel._notifications import NoticeKind
from heinzel.kernel._signals import DAYPARTS, NUMERIC_SIGNALS
from heinzel.kernel._themes import _read_regular  # pyright: ignore[reportPrivateUsage] - one reader for theme and sound files

SOUNDSCAPE_SUFFIX = ".toml"
OFF = "off"
"""The name of no sound: always there, never offered by a plugin."""
NAME = r"^[a-z](?:-?[a-z0-9])*$"
MAX_NAME = 64
MAX_TITLE = 64
MAX_LINE = 200
MAX_LAYERS = 8
MAX_PAIRS = 8
MAX_CURVE = 8
MAX_ASSET_BYTES = 2 * 1024 * 1024
"""The largest audio file a soundscape may ship, 2 MB."""
MAX_SOUNDSCAPE_BYTES = 8 * 1024 * 1024
"""All audio files of one soundscape together, 8 MB."""
AUDIO_FILE = r"^(?:[A-Za-z0-9_][A-Za-z0-9._-]*/){0,7}[A-Za-z0-9_][A-Za-z0-9._-]*\.(?:ogg|opus|mp3)$"
"""A relative path of plain names inside the soundscape's directory ending in `.ogg`, `.opus` or `.mp3`."""
NEEDS_ATTRIBUTION = frozenset({"CC-BY-3.0", "CC-BY-4.0"})
SOUND_ROUTE = "/sound-assets"
"""Where the web head serves soundscapes' audio files, outside `/api`: `layer-<n>.<ext>` and `earcon-<kind>.<ext>`."""
SOUND_CSP = "default-src 'none'; sandbox"
"""The Content Security Policy of a served audio file: opened on its own, it loads and runs nothing."""
VIDEO = r"^[A-Za-z0-9_-]{11}$"
_OGG = b"OggS"
_ID3 = b"ID3"
_MP3_FRAME_HEADER = 4
_MP3_BITRATES = {
    3: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),  # MPEG-1 Layer III
    2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),  # MPEG-2 Layer III
    0: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),  # MPEG-2.5 Layer III
}
_MP3_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}
_TYPES = {"ogg": "audio/ogg", "opus": "audio/ogg", "mp3": "audio/mpeg"}


@dataclass(frozen=True, slots=True)
class StreamProvider:
    """A host whose official player a stream soundscape plays in: the frame origin and how a video's URLs look."""

    frame_origin: str
    """The only origin the page's CSP admits as a frame, while a stream of this provider is in effect."""
    embed: str
    """The embed URL with `{video}` for the id."""
    watch: str
    """Where a person opens the video on the host itself, with `{video}`."""

    def embed_url(self, video: str) -> str:
        """The embed URL of `video`."""
        return self.embed.format(video=video)

    def watch_url(self, video: str) -> str:
        """The host's own page of `video`."""
        return self.watch.format(video=video)


STREAM_PROVIDERS: Mapping[str, StreamProvider] = {
    # The privacy-enhanced embed: no cookie before the person plays (ADR 0058).
    "youtube": StreamProvider(
        frame_origin="https://www.youtube-nocookie.com",
        embed="https://www.youtube-nocookie.com/embed/{video}",
        watch="https://www.youtube.com/watch?v={video}",
    ),
}
"""The hosts a stream may come from, in code: a plugin or a configuration file can't add one."""


def _one_line(value: str) -> str:
    if any(not character.isprintable() for character in value):
        msg = "one line of printable characters"
        raise ValueError(msg)
    return value


def _https(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        msg = "an https URL, e.g. https://freesound.org/s/12345/"
        raise ValueError(msg) from exc
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or not value.isprintable():
        msg = "an https URL without credentials, e.g. https://freesound.org/s/12345/"
        raise ValueError(msg)
    return value


Line = Annotated[str, AfterValidator(_one_line), Field(max_length=MAX_LINE)]
AudioFile = Annotated[str, Field(pattern=AUDIO_FILE, max_length=MAX_LINE)]


class SoundAsset(BaseModel):
    """An audio file a soundscape ships, with the licence it is under."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    file: AudioFile = Field(
        description="The `.ogg`, `.opus` or `.mp3` file, relative to the soundscape file's directory, e.g. "
        "`sounds/rain.ogg`."
    )
    licence: Literal["CC0-1.0", "PDDL-1.0", "CC-PDDC", "CC-BY-3.0", "CC-BY-4.0"] = Field(
        description="Its licence, an SPDX identifier: `CC0-1.0`, `PDDL-1.0`, `CC-PDDC`, `CC-BY-3.0` or `CC-BY-4.0`."
    )
    attribution: Line | None = Field(
        default=None, description="Who made it, as the licence wants it shown; required for CC-BY."
    )
    source: Annotated[str, AfterValidator(_https)] | None = Field(
        default=None, max_length=MAX_LINE, description="Where it comes from, an https URL."
    )

    @model_validator(mode="after")
    def _attributed(self) -> Self:
        if self.licence in NEEDS_ATTRIBUTION and not self.attribution:
            msg = f"{self.licence} needs an attribution: who made the sound, as the licence wants it shown"
            raise ValueError(msg)
        return self


class SoundEarcon(SoundAsset):
    """A short sound the web app plays when a notice of its kind arrives while sound plays, at most 5 seconds."""


class SoundLayer(SoundAsset):
    """One layer of a layered soundscape: a looped file whose gain follows a signal through a curve."""

    gain: float = Field(default=1.0, ge=0, le=1, description="Its loudness, 0 to 1, before the curve.")
    signal: Literal["none", "daypart", "weather.rain", "weather.wind"] = Field(
        default="none", description="The signal its gain follows: `none`, `daypart`, `weather.rain` or `weather.wind`."
    )
    curve: tuple[tuple[float, float], ...] | dict[str, float] | None = Field(
        default=None,
        description="For `weather.*`: 2 to 8 points `[x, y]` in 0..1, x rising, read piecewise linear; for `daypart`: "
        "`{night, morning, afternoon, evening}` to 0..1, a missing part is 0; none for `none`.",
    )

    @model_validator(mode="after")
    def _curve(self) -> Self:
        if self.signal == "none":
            if self.curve is not None:
                msg = "a layer without a signal has no curve"
                raise ValueError(msg)
            return self
        if self.curve is None:
            msg = f"a layer that follows {self.signal} needs a curve"
            raise ValueError(msg)
        if self.signal in NUMERIC_SIGNALS:
            _points(self.curve)
        else:
            _parts(self.curve)
        return self


def _points(curve: tuple[tuple[float, float], ...] | dict[str, float]) -> None:
    if not isinstance(curve, tuple) or not 2 <= len(curve) <= MAX_CURVE:  # noqa: PLR2004 - a line needs two points
        msg = f"a curve of a weather signal is 2 to {MAX_CURVE} points [x, y], e.g. [[0, 0], [1, 1]]"
        raise ValueError(msg)
    xs = [x for x, _ in curve]
    if any(not (0 <= value <= 1) for point in curve for value in point) or xs != sorted(set(xs)):
        msg = "a curve's points are in 0..1 and its x values rise, e.g. [[0, 0], [0.5, 0.8], [1, 1]]"
        raise ValueError(msg)


def _parts(curve: tuple[tuple[float, float], ...] | dict[str, float]) -> None:
    if not isinstance(curve, dict) or not curve:
        msg = "a curve of daypart is a table of night, morning, afternoon and evening, e.g. {morning = 1}"
        raise ValueError(msg)
    if unknown := sorted(set(curve) - set(DAYPARTS)):
        msg = f"{', '.join(unknown)} isn't a part of the day: night, morning, afternoon or evening"
        raise ValueError(msg)
    if any(not (0 <= value <= 1) for value in curve.values()):
        msg = "a curve's gains are 0 to 1"
        raise ValueError(msg)


def _provider(value: str) -> str:
    if value not in STREAM_PROVIDERS:
        msg = f"the provider is one of {', '.join(STREAM_PROVIDERS)}"
        raise ValueError(msg)
    return value


class SoundStream(BaseModel):
    """A stream in the official player of an allowed host: a provider of `STREAM_PROVIDERS` and the video's id."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    provider: Annotated[str, AfterValidator(_provider)] = Field(description="The host, one of `STREAM_PROVIDERS`.")
    video: str = Field(pattern=VIDEO, description="The video's id, e.g. `tRsQsTMvPNg`.")


class Soundscape(BaseModel):
    """Background sound for the web app: layered audio files or a stream (ADR 0058)."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    name: str = Field(pattern=NAME, max_length=MAX_NAME, description="Lowercase words joined by dashes, e.g. `rain`.")
    title: Annotated[str, AfterValidator(_one_line)] = Field(
        min_length=1, max_length=MAX_TITLE, description="What people pick it by, e.g. `Rainy study`."
    )
    kind: Literal["layers", "stream"] = Field(description="`layers`: audio files mixed live; `stream`: a stream.")
    pairs_with: tuple[Annotated[str, Field(pattern=NAME, max_length=MAX_NAME)], ...] = Field(
        default=(),
        max_length=MAX_PAIRS,
        description="Themes it goes with: with `follow_theme` on, it is in effect while one of them is.",
    )
    fade_seconds: int = Field(default=4, ge=1, le=30, strict=True, description="How long a change fades, 1 to 30.")
    layers: tuple[SoundLayer, ...] = Field(
        default=(), max_length=MAX_LAYERS, description="The layers of a layered soundscape, 1 to 8."
    )
    earcons: dict[NoticeKind, SoundEarcon] = Field(
        default_factory=dict[NoticeKind, SoundEarcon],
        description="Short sounds by notice kind (`draft`, `failed`, ...), for a layered soundscape.",
    )
    stream: SoundStream | None = Field(default=None, description="The stream of a stream soundscape.")

    @model_validator(mode="after")
    def _kind(self) -> Self:
        if self.kind == "layers" and (self.stream is not None or not self.layers):
            msg = "a layered soundscape has 1 to 8 [[layers]] and no [stream]"
            raise ValueError(msg)
        if self.kind == "stream" and (self.stream is None or self.layers or self.earcons):
            msg = "a stream soundscape has a [stream] and no [[layers]] or [earcons]"
            raise ValueError(msg)
        return self

    def assets(self) -> dict[str, SoundAsset]:
        """Every audio file by the name the web head serves it under, without extension: `layer-0`, `earcon-draft`."""
        named: dict[str, SoundAsset] = {f"layer-{index}": layer for index, layer in enumerate(self.layers)}
        named |= {f"earcon-{kind.value}": earcon for kind, earcon in self.earcons.items()}
        return named


def audio_type(content: bytes, extension: str) -> str | None:
    """The media type of `content` if its signature matches `extension` (`ogg`, `opus`, `mp3`), else None.

    `.ogg` and `.opus` start with `OggS`. `.mp3` starts with an ID3 tag or with two consecutive valid MPEG Layer III
    frame headers: one 11-bit sync word alone is too weak a signature.
    """
    if extension in {"ogg", "opus"}:
        return _TYPES[extension] if content.startswith(_OGG) else None
    if extension == "mp3" and (content.startswith(_ID3) or _mp3_frames(content)):
        return _TYPES[extension]
    return None


def _mp3_frame_length(header: bytes) -> int | None:
    """The length of the MPEG Layer III frame that `header` (4 bytes) starts, or None if it isn't a valid header."""
    if len(header) < _MP3_FRAME_HEADER or header[0] != 0xFF or header[1] & 0xE0 != 0xE0:  # noqa: PLR2004 - sync word
        return None
    version, layer = (header[1] >> 3) & 3, (header[1] >> 1) & 3
    bitrate_index, rate_index, padding = header[2] >> 4, (header[2] >> 2) & 3, (header[2] >> 1) & 1
    if version not in _MP3_BITRATES or layer != 1 or bitrate_index in {0, 15} or rate_index == 3:  # noqa: PLR2004
        return None
    bitrate = _MP3_BITRATES[version][bitrate_index] * 1000
    rate = _MP3_RATES[version][rate_index]
    return (144 if version == 3 else 72) * bitrate // rate + padding  # noqa: PLR2004 - MPEG-1 has 1152 samples


def _mp3_frames(content: bytes) -> bool:
    first = _mp3_frame_length(content[:_MP3_FRAME_HEADER])
    return first is not None and _mp3_frame_length(content[first : first + _MP3_FRAME_HEADER]) is not None


def read_audio(directory: Path, asset: SoundAsset) -> tuple[bytes, str]:
    """The content of `asset` and its media type, checked as it is read, so a file swapped meanwhile is caught.

    Raises:
        ValueError: If the file is missing, leads out of the directory, isn't a regular file, is larger than 2 MB, or
            its signature doesn't match its extension.
    """
    where = f"Audio file {asset.file!r} of {directory}"
    if not re.fullmatch(AUDIO_FILE, asset.file):
        msg = f"{where} isn't a path inside the soundscape's directory ending in .ogg, .opus or .mp3"
        raise ValueError(msg)
    try:
        root = directory.resolve(strict=True)
        path = (root / asset.file).resolve(strict=True)
    except OSError as exc:
        msg = f"{where} doesn't exist; its path is relative to the soundscape file's directory"
        raise ValueError(msg) from exc
    if not path.is_relative_to(root):
        msg = f"{where} leads out of the soundscape's directory; keep audio files beside the soundscape file"
        raise ValueError(msg)
    if not path.is_file():
        msg = f"{where} isn't a file"
        raise ValueError(msg)
    content = _read_regular(path, MAX_ASSET_BYTES, where)
    if len(content) > MAX_ASSET_BYTES:
        msg = f"{where} is larger than {MAX_ASSET_BYTES // 1024 // 1024} MB; shorten or compress it"
        raise ValueError(msg)
    media_type = audio_type(content, asset.file.rpartition(".")[2])
    if media_type is None:
        msg = f"{where} isn't the audio its extension says: .ogg and .opus are Ogg files, .mp3 is MPEG audio"
        raise ValueError(msg)
    return content, media_type


@dataclass(frozen=True, kw_only=True, slots=True)
class SoundscapeLet(Let):
    """A soundscape a module offers (`module.soundscape`), named after it; consumed by `system.sound`."""

    soundscape: Soundscape
    directory: Path | None = None
    """The directory of the soundscape's file, which its audio files are relative to; None for one built in Python."""

    def __post_init__(self) -> None:
        """Check the soundscape again, so one built with `model_construct` can't skip the checks, and its files.

        Raises:
            ValueError: If the soundscape isn't valid, is called `off`, the let isn't named after it, or an audio
                file doesn't pass the checks of `read_audio`, or all of them are larger than 8 MB.
        """
        try:
            soundscape = Soundscape.model_validate(self.soundscape.model_dump())
        except ValidationError as exc:
            msg = f"Soundscape {self.soundscape.name!r} isn't valid: {problems_of(exc)}"
            raise ValueError(msg) from exc
        if soundscape.name == OFF:
            msg = f"No plugin can offer a soundscape named {OFF!r}: that is no sound"
            raise ValueError(msg)
        if self.name != soundscape.name:
            msg = f"A soundscape let is named after its soundscape: {soundscape.name!r}, not {self.name!r}"
            raise ValueError(msg)
        assets = soundscape.assets()
        if assets and self.directory is None:
            msg = f"Soundscape {soundscape.name!r} ships audio files, so it comes from a soundscape file"
            raise ValueError(msg)
        total = sum(len(read_audio(self.directory, asset)[0]) for asset in assets.values()) if self.directory else 0
        if total > MAX_SOUNDSCAPE_BYTES:
            msg = f"Soundscape {soundscape.name!r} ships more than {MAX_SOUNDSCAPE_BYTES // 1024 // 1024} MB of audio"
            raise ValueError(msg)
        object.__setattr__(self, "soundscape", soundscape)

    def asset(self, part: str) -> tuple[bytes, str, str]:
        """The content, media type and extension of asset `part` (`layer-0`, `earcon-draft`), checked as it is read.

        Raises:
            ValueError: If there is no such asset, or its file no longer passes the checks of `read_audio`.
        """
        asset = self.soundscape.assets().get(part)
        if asset is None or self.directory is None:
            msg = f"Soundscape {self.soundscape.name!r} has no {part!r}"
            raise ValueError(msg)
        content, media_type = read_audio(self.directory, asset)
        return content, media_type, asset.file.rpartition(".")[2]

    def asset_url(self, part: str) -> str:
        """Where the web head serves asset `part`, with a version of its content, so a changed file is a new URL.

        Raises:
            ValueError: If there is no such asset, or its file no longer passes the checks of `read_audio`.
        """
        content, _, extension = self.asset(part)
        version = hashlib.sha256(content).hexdigest()[:16]
        return f"{SOUND_ROUTE}/{self.soundscape.name}/{part}.{extension}?v={version}"


class SoundAssets:
    """The audio files of the available soundscapes and the stream in effect; provided by `system.sound` (ADR 0058).

    The web head serves the files at `SOUND_ROUTE` and widens its page's CSP by `stream_provider`; the kernel declares
    the interface, so the head needn't know the module.
    """

    def asset(self, soundscape: str, part: str, extension: str) -> tuple[bytes, str] | None:
        """Content and media type of asset `part` of the available `soundscape` if its file ends in `extension`."""
        raise NotImplementedError

    def stream_provider(self) -> str | None:
        """The provider of the stream soundscape in effect, a key of `STREAM_PROVIDERS`; None for none or layers."""
        raise NotImplementedError


class ThemeChoice:
    """The theme in effect, by name; provided by `system.theme`, read by `system.sound` to follow it (ADR 0058)."""

    def selected(self) -> str:
        """The name of the theme in effect, after every fallback; `default` when nothing else is."""
        raise NotImplementedError


def problems_of(exc: ValidationError) -> str:
    """Each problem of a soundscape as `field: rule`, in words a person editing a soundscape file reads."""
    rules = {
        "name": "lowercase words joined by single dashes, e.g. rain",
        "file": "a path inside the soundscape's directory ending in .ogg, .opus or .mp3, e.g. sounds/rain.ogg",
        "video": "the video's id: 11 letters, digits, dashes or underscores, e.g. tRsQsTMvPNg",
    }
    problems: list[str] = []
    for error in exc.errors():
        where = ".".join(map(str, error["loc"])) or "soundscape"
        field = next((str(part) for part in reversed(error["loc"]) if isinstance(part, str)), "")
        if error["type"] == "string_pattern_mismatch":
            rule = rules.get(field, "lowercase words joined by single dashes, e.g. acme")
        else:
            rule = error["msg"].removeprefix("Value error, ")
        problems.append(f"{where}: {rule}")
    return "; ".join(problems)


def sourced_soundscapes(source: Path | Soundscape) -> tuple[tuple[Soundscape, Path | None], ...]:
    """Each soundscape of `source` (one, a `*.toml` file of one, or a directory of them) with its file's directory.

    Raises:
        ValueError: If a file isn't a valid soundscape, or a directory holds none.
        OSError: If a file can't be read.
    """
    if isinstance(source, Soundscape):
        return ((source, None),)
    files = sorted(source.glob(f"*{SOUNDSCAPE_SUFFIX}")) if source.is_dir() else [source]
    if not files:
        msg = f"{source} holds no soundscape files (*{SOUNDSCAPE_SUFFIX})"
        raise ValueError(msg)
    found: list[tuple[Soundscape, Path | None]] = []
    for file in files:
        if file.suffix != SOUNDSCAPE_SUFFIX:
            msg = f"Soundscape file {file} must end in {SOUNDSCAPE_SUFFIX}"
            raise ValueError(msg)
        try:
            found.append((Soundscape.model_validate(tomllib.loads(file.read_text(encoding="utf-8"))), file.parent))
        except tomllib.TOMLDecodeError as exc:
            msg = f"Soundscape file {file} isn't valid TOML: {exc}"
            raise ValueError(msg) from exc
        except ValidationError as exc:
            msg = f"Soundscape file {file} isn't a valid soundscape: {problems_of(exc)}"
            raise ValueError(msg) from exc
    return tuple(found)
