"""Service of the sound module: the soundscapes plugins offer, ties by layer order, and `[system.sound]` (ADR 0058)."""

from dataclasses import dataclass, field
from datetime import datetime

from pydantic import ValidationError

from heinzel import (
    Config,
    ConfigEditor,
    HeinzelError,
    Kernel,
    LayerStack,
    ModuleRecord,
    SoundAssets,
    SoundscapeLet,
    ThemeChoice,
)
from heinzel.kernel import STREAM_PROVIDERS, daypart
from heinzel.modules.system.sound._models import (
    SoundAssetView,
    SoundEarconView,
    SoundLayerView,
    SoundList,
    SoundscapeView,
    SoundSettings,
    SoundStreamView,
)

SECTION = "system.sound"
KEY = f"{SECTION}.name"
OFF = "off"


@dataclass(frozen=True, slots=True)
class Offered:
    """A soundscape in effect under its name, the module offering it and the modules whose one it hides."""

    let: SoundscapeLet
    record: ModuleRecord
    rank: tuple[int, str]
    shadows: list[str] = field(default_factory=list[str])


@dataclass(frozen=True, slots=True)
class Available:
    """The soundscapes by name, and the names an org and the site both offer with nothing more specific deciding."""

    soundscapes: dict[str, Offered]
    conflicts: list[str]


@dataclass(frozen=True, slots=True)
class Choice:
    """What is configured and what is in effect, with why they differ."""

    settings: SoundSettings
    selected: str
    effective: str
    note: str | None
    next: list[str]


def _branch(stack: LayerStack, layer: str) -> str | None:
    """`site` for the site, `org` for a layer beside it on the org line, None for the company and the team."""
    if layer == stack.site:
        return "site"
    if stack.site is None or layer in stack.site_ancestors or layer == stack.layer:
        return None
    return "org"


def _asset_view(let: SoundscapeLet, part: str) -> SoundAssetView | None:
    """Where an asset is served, with its licence; None when its file is gone since import."""
    asset = let.soundscape.assets()[part]
    try:
        url = let.asset_url(part)
    except ValueError:
        return None
    return SoundAssetView(url=url, licence=asset.licence, attribution=asset.attribution, source=asset.source)


def _view(name: str, offered: Offered, choice: Choice) -> SoundscapeView:
    soundscape = offered.let.soundscape
    stream = None
    if soundscape.stream is not None:
        provider = STREAM_PROVIDERS[soundscape.stream.provider]
        stream = SoundStreamView(
            provider=soundscape.stream.provider,
            video=soundscape.stream.video,
            embed_url=provider.embed_url(soundscape.stream.video),
            frame_origin=provider.frame_origin,
            watch_url=provider.watch_url(soundscape.stream.video),
        )
    layers: list[SoundLayerView] = []
    for index, layer in enumerate(soundscape.layers):
        if (asset := _asset_view(offered.let, f"layer-{index}")) is None:
            continue
        curve = [list(point) for point in layer.curve] if isinstance(layer.curve, tuple) else layer.curve
        layers.append(
            SoundLayerView(**asset.model_dump(), gain=layer.gain, signal=layer.signal, curve=curve),
        )
    earcons = [
        SoundEarconView(**asset.model_dump(), kind=kind.value)
        for kind in soundscape.earcons
        if (asset := _asset_view(offered.let, f"earcon-{kind.value}")) is not None
    ]
    return SoundscapeView(
        name=name,
        title=soundscape.title,
        kind=soundscape.kind,
        module=offered.record.qualname,
        plugin=offered.record.origin.distribution,
        selected=name == choice.selected,
        effective=name == choice.effective,
        pairs_with=list(soundscape.pairs_with),
        fade_seconds=soundscape.fade_seconds,
        stream=stream,
        layers=layers,
        earcons=earcons,
        shadows=offered.shadows,
    )


class SoundService(SoundAssets):
    """The soundscapes of the web app (ADR 0058): no sound, core soundscapes and every one a plugin offers."""

    def __init__(self, kernel: Kernel, editor: ConfigEditor) -> None:
        """Create the service; the kernel and the configuration editor are injected."""
        self._kernel = kernel
        self._editor = editor

    def available(self) -> Available:
        """Each soundscape by name; of several modules offering one name, the most specific layer of the stack wins.

        The rule is the themes' (ADR 0044): the team over the site and the orgs, those over the company, any layer
        over a module outside the stack, then the first module by qualified name; an org and the site offering one
        name with nothing more specific deciding is a conflict, left out and reported.
        """
        stack = self._kernel.layer_stack()
        rank = {name: index for index, name in enumerate(stack.names)}

        def order(record: ModuleRecord) -> tuple[int, str]:
            top = record.path[0]
            return (-1 - rank[top] if top in rank else 0, record.qualname)

        by_name: dict[str, list[tuple[ModuleRecord, SoundscapeLet]]] = {}
        for record, let in sorted(self._kernel.lets(SoundscapeLet), key=lambda pair: order(pair[0])):
            by_name.setdefault(let.name, []).append((record, let))
        soundscapes: dict[str, Offered] = {}
        conflicts: list[str] = []
        for name, offers in by_name.items():
            (winner, let), *others = offers
            branch = _branch(stack, winner.path[0]) if winner.path[0] in rank else None
            rivals = [
                r.path[0] for r, _ in others if r.path[0] in rank and _branch(stack, r.path[0]) not in {None, branch}
            ]
            if branch is not None and rivals:
                conflicts.append(
                    f"{name}: offered by {winner.path[0]} and {', '.join(rivals)}, an org and the site; nobody "
                    f"decided which applies, so neither does until {stack.layer or 'your team'} offers it"
                )
                continue
            soundscapes[name] = Offered(let, winner, order(winner), [r.qualname for r, _ in others])
        # The core's own soundscapes first, then the plugins' in the order above.
        ordered = sorted(soundscapes.items(), key=lambda item: not item[1].record.origin.builtin)
        return Available(dict(ordered), conflicts)

    def _theme(self) -> str | None:
        """The theme in effect, or None when no module provides it (the theme module is off)."""
        if not self._kernel.provides(ThemeChoice):
            return None
        try:
            return self._kernel.resolve(ThemeChoice).selected()
        except HeinzelError:
            return None

    def _choice(self, available: Available) -> Choice:
        """What `[system.sound]` configures and what is in effect, read now: a running `hz ui` sees a terminal's pick.

        Raises:
            HeinzelError: If a configuration file is not valid TOML.
        """
        config = Config.load()
        note, next_steps = None, list[str]()
        try:
            settings = config.settings(SoundSettings, SECTION)
        except ValidationError as exc:
            settings = SoundSettings()
            problems = "; ".join(
                f"{'.'.join(map(str, error['loc'])) or 'the table'}: {error['msg']}" for error in exc.errors()
            )
            note = (
                f"`[system.sound]` (or an HZ_SYSTEM_SOUND__ variable) isn't valid ({problems}); no sound plays and "
                "the defaults apply until it is fixed."
            )
            next_steps = ["hz system config reveal user"]  # show, get and unset fail while a value is invalid
        selected = settings.name
        if selected != OFF and selected not in available.soundscapes:
            note = (
                f"No installed plugin offers the soundscape {selected!r}; no sound plays until you pick another or "
                "install its plugin."
            )
            next_steps = [f"hz system sound use {OFF}"]
            selected = OFF
        effective = selected
        theme = self._theme() if settings.follow_theme else None
        if theme is not None:
            paired = sorted(
                (offered.rank[0], name)  # the most specific layer, then the soundscape's name (ADR 0058)
                for name, offered in available.soundscapes.items()
                if theme in offered.let.soundscape.pairs_with
            )
            if paired:
                effective = paired[0][1]
                if effective != selected:
                    note = f"The theme {theme!r} brings its own soundscape, {effective!r}: follow_theme is on."
        if not next_steps:
            next_steps = [f"hz system sound use {name}" for name in available.soundscapes if name != selected][:1]
        return Choice(settings, selected, effective, note, next_steps)

    def listing(self) -> SoundList:
        """Every available soundscape, the one configured and the one in effect, and how it plays.

        Raises:
            HeinzelError: If a configuration file is not valid TOML.
        """
        available = self.available()
        choice = self._choice(available)
        off = SoundscapeView(
            name=OFF,
            title="Off",
            kind="off",
            module=None,
            plugin=None,
            selected=choice.selected == OFF,
            effective=choice.effective == OFF,
        )
        return SoundList(
            selected=choice.selected,
            effective=choice.effective,
            soundscapes=[off, *(_view(name, offered, choice) for name, offered in available.soundscapes.items())],
            signals={"daypart": daypart(datetime.now().astimezone())},
            volume=choice.settings.volume,
            follow_theme=choice.settings.follow_theme,
            mute_in_meetings=choice.settings.mute_in_meetings,
            duck_while_typing=choice.settings.duck_while_typing,
            conflicts=available.conflicts,
            note=choice.note,
            next=choice.next,
        )

    def use(self, name: str) -> SoundList:
        """Pick `name` for playback, turn off theme following, then list the soundscapes.

        Raises:
            HeinzelError: If no installed plugin offers a soundscape of that name, or the file can't be written.
        """
        soundscapes = self.available().soundscapes
        if name != OFF and name not in soundscapes:
            msg = (
                f"No soundscape is called {name!r}; available: {', '.join([OFF, *soundscapes])}. Pick one, e.g. "
                f"`hz system sound use {OFF}`, or install the plugin that offers it."
            )
            raise HeinzelError(msg)
        self._editor.write(KEY, name, "user")
        self._editor.write(f"{SECTION}.follow_theme", value=False, scope="user")
        return self.listing()

    def asset(self, soundscape: str, part: str, extension: str) -> tuple[bytes, str] | None:
        """Content and media type of asset `part` of the available `soundscape`, if its file ends in `extension`."""
        offered = self.available().soundscapes.get(soundscape)
        if offered is None:
            return None
        try:
            content, media_type, actual = offered.let.asset(part)
        except ValueError:
            return None
        return (content, media_type) if actual == extension else None

    def stream_provider(self) -> str | None:
        """The provider of the stream soundscape in effect; None when there is no sound or it is layered."""
        available = self.available()
        offered = available.soundscapes.get(self._choice(available).effective)
        stream = offered.let.soundscape.stream if offered else None
        return stream.provider if stream else None
