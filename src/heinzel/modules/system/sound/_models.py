"""Models and settings of the sound module."""

from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from heinzel import ModuleSettings

NAME_PATTERN = r"^[a-z](?:-?[a-z0-9])*$"


class SoundSettings(ModuleSettings):
    """Which soundscape the web app plays and how: the `[system.sound]` table or `HZ_SYSTEM_SOUND__<KEY>`.

    User scope, every key: a repository someone else controls must not switch on a stream from another host, widen the
    page's CSP or decide how loud a person's tool is.
    """

    ignored_in_project: ClassVar[frozenset[str]] = frozenset(
        {"name", "volume", "follow_theme", "mute_in_meetings", "duck_while_typing"}
    )

    name: str = Field(
        default="off",
        pattern=NAME_PATTERN,
        max_length=64,
        description="The web app's soundscape, one `hz system sound list` shows, e.g. `claude-fm`; `off` for none.",
    )
    volume: int = Field(default=50, ge=0, le=100, description="How loud, 0 to 100.")
    follow_theme: bool = Field(
        default=False,
        description="Play the soundscape that goes with the theme in effect, when one does, instead of `name`.",
    )
    mute_in_meetings: bool = Field(
        default=True, description="Mute while your calendar says you are in a meeting; needs a connected calendar."
    )
    duck_while_typing: bool = Field(default=True, description="Play softer while you type in the chat.")


class SoundAssetView(BaseModel):
    """An audio file of a layered soundscape, where the web head serves it, and its licence."""

    url: str = Field(description="Where the web head serves it, e.g. `/sound-assets/rain/layer-0.ogg?v=1a2b3c4d`.")
    licence: str = Field(description="Its licence, an SPDX identifier, e.g. `CC0-1.0`.")
    attribution: str | None = Field(description="Who made it, to show beside it; set for CC-BY.")
    source: str | None = Field(description="Where it comes from, an https URL.")


class SoundLayerView(SoundAssetView):
    """One layer of a layered soundscape: a looped file whose gain follows a signal."""

    gain: float = Field(description="Its loudness, 0 to 1, before the curve.")
    signal: str = Field(description="The signal its gain follows: `none`, `daypart`, `weather.rain`, `weather.wind`.")
    curve: list[list[float]] | dict[str, float] | None = Field(
        description="Points `[x, y]` for a weather signal, gains by part of the day for `daypart`; none for `none`."
    )


class SoundEarconView(SoundAssetView):
    """A short sound for notices of one kind."""

    kind: str = Field(description="The notice kind it plays for, e.g. `draft` or `failed`.")


class SoundStreamView(BaseModel):
    """A stream in the official player of an allowed host."""

    provider: str = Field(description="The host, e.g. `youtube`.")
    video: str = Field(description="The video's id, e.g. `tRsQsTMvPNg`.")
    embed_url: str = Field(
        description="The player to frame after Play, e.g. `https://www.youtube-nocookie.com/embed/...`."
    )
    frame_origin: str = Field(description="The origin the page's CSP admits as a frame while it is in effect.")
    watch_url: str = Field(description="Where the video is on the host itself, to open it there instead.")


class SoundscapeView(BaseModel):
    """One available soundscape, ready for the web app to play."""

    name: str = Field(description="What `hz system sound use` takes, e.g. `claude-fm`; `off` is no sound.")
    title: str = Field(description="What people pick it by, e.g. `Claude FM`.")
    kind: Literal["off", "layers", "stream"] = Field(description="`off`, `layers` (files mixed live) or `stream`.")
    module: str | None = Field(description="The qualified name of the module offering it; none for `off`.")
    plugin: str | None = Field(description="The plugin it comes with; none for `off` and the core's own.")
    selected: bool = Field(description="Whether it is the one configured.")
    effective: bool = Field(description="Whether it is the one in effect (follow my theme may pick another).")
    pairs_with: list[str] = Field(default_factory=list[str], description="Themes it goes with.")
    fade_seconds: int = Field(default=4, description="How long a change fades, in seconds.")
    stream: SoundStreamView | None = Field(default=None, description="The stream; none unless the kind is `stream`.")
    layers: list[SoundLayerView] = Field(default_factory=list[SoundLayerView], description="The layers to mix.")
    earcons: list[SoundEarconView] = Field(
        default_factory=list[SoundEarconView], description="Short sounds for notices, by kind."
    )
    shadows: list[str] = Field(
        default_factory=list[str],
        description="Modules that offer a soundscape of the same name, hidden by this one (layer order decides).",
    )


class SoundList(BaseModel):
    """Every available soundscape, which one is configured and which is in effect, and how it plays."""

    selected: str = Field(description="The name of the soundscape configured, after fallbacks; `off` for none.")
    effective: str = Field(description="The name of the soundscape in effect: `selected`, or the theme's own.")
    soundscapes: list[SoundscapeView] = Field(description="`off` first, then the core's and the plugins' soundscapes.")
    signals: dict[str, str | float] = Field(
        description="The signals layers follow, now: `daypart` always, `weather.*` once a plugin publishes them."
    )
    volume: int = Field(description="How loud, 0 to 100.")
    follow_theme: bool = Field(description="Whether the soundscape that goes with the theme in effect plays instead.")
    mute_in_meetings: bool = Field(description="Whether sound mutes during a meeting in your calendar.")
    duck_while_typing: bool = Field(description="Whether sound plays softer while you type in the chat.")
    conflicts: list[str] = Field(
        default_factory=list[str],
        description="Names an org and the site both offer with nothing more specific deciding; left out until one is.",
    )
    note: str | None = Field(default=None, description="Why the soundscape in effect isn't the one configured.")
    next: list[str] = Field(default_factory=list[str], description="What to run next.")
