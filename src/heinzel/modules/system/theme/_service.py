"""Service of the theme module: the themes plugins offer, ties decided by layer order, and `[system.theme] name`."""

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo, available_timezones

from pydantic import ValidationError

from heinzel import (
    Config,
    ConfigEditor,
    HeinzelError,
    Kernel,
    LayerStack,
    ModuleRecord,
    Theme,
    ThemeChoice,
    ThemeFonts,
    ThemeLet,
)
from heinzel.kernel import (
    DEFAULT_LOGO,
    DEFAULT_THEME,
    OperationLet,
    SignalLet,
    ThemeLogoFiles,
    ThemeSignals,
    Weather,
    daypart,
)
from heinzel.modules.system.theme._models import (
    ThemeFontView,
    ThemeList,
    ThemeLogoView,
    ThemeSettings,
    ThemeSignalsView,
    ThemeView,
)

logger = logging.getLogger(__name__)

SECTION = "system.theme"
KEY = f"{SECTION}.name"
VARIABLE = "HZ_SYSTEM_THEME__NAME"
DEADLINE = timedelta(seconds=2)
"""How long a module's signals operation may take before its signals stay unknown for the interval."""
LOCALTIME = Path("/etc/localtime")
REFRESH = timedelta(minutes=1)
"""How often the web app asks for the signals: the daypart moves on the minute."""


@dataclass(frozen=True, slots=True)
class Offered:
    """A theme in effect under its name, the module offering it and the modules whose same-named theme it hides."""

    theme: Theme
    record: ModuleRecord | None
    shadows: list[str] = field(default_factory=list[str])
    let: ThemeLet | None = None
    logos: ThemeLogoFiles | None = None


@dataclass(frozen=True, slots=True)
class Available:
    """The themes by name, and the names an org and the site both offer with nothing more specific deciding."""

    themes: dict[str, Offered]
    conflicts: list[str]


def _branch(stack: LayerStack, layer: str) -> str | None:
    """`site` for the site, `org` for a layer beside it on the org line, None for the company and the team."""
    if layer == stack.site:
        return "site"
    if stack.site is None or layer in stack.site_ancestors or layer == stack.layer:
        return None
    return "org"


def _fonts(offered: Offered) -> list[ThemeFontView]:
    """The fonts of a theme with the URLs the web head serves them at; a file gone since import is left out."""
    views: list[ThemeFontView] = []
    for index, font in enumerate(offered.theme.fonts):
        if offered.let is None:
            break
        try:
            url = offered.let.font_url(index)
        except ValueError:
            continue
        views.append(ThemeFontView(family=font.family, url=url, weight=font.weight, style=font.style))
    return views


def _logo(logos: ThemeLogoFiles | None) -> ThemeLogoView | None:
    """A theme's logo with the URLs the web head serves it at; none if its light file is gone since import.

    A dark logo whose file is gone falls back to the light one, a mark whose file is gone is left out.
    """
    if logos is None:
        return None
    try:
        light = logos.url("light")
    except ValueError:
        return None
    try:
        dark = logos.url("dark") if logos.logo.dark else light
    except ValueError:
        dark = light
    try:
        mark = logos.url("mark") if logos.logo.mark else None
    except ValueError:
        mark = None
    return ThemeLogoView(light=light, dark=dark, mark=mark, alt=logos.logo.alt, height=logos.logo.height)


class ThemeService(ThemeFonts, ThemeChoice):
    """The themes of the web app (ADR 0044): the built-in one and every theme the installed plugins offer."""

    def __init__(self, kernel: Kernel, editor: ConfigEditor) -> None:
        """Create the service; the kernel and the configuration editor are injected."""
        self._kernel = kernel
        self._editor = editor
        self._read: dict[str, tuple[datetime, ThemeSignals | None]] = {}
        self._lock = Lock()
        # Few workers: a hanging plugin holds one of them, never the web head's request threads.
        self._workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hz-signals")

    def available(self) -> Available:
        """Each theme by name; of several modules offering one name, the most specific layer of the stack wins.

        The order is the person's stack, general to specific (ADR 0040): the team over the site and the orgs, those
        over the company, any layer over a module outside the stack, then the first module by qualified name. An org
        and the site that both offer a name, with neither the team nor anything more specific offering it, are a
        conflict nobody decided (ADR 0040's open org-site question): the name is left out and reported.
        """
        stack = self._kernel.layer_stack()
        rank = {name: index for index, name in enumerate(stack.names)}

        def order(pair: tuple[ModuleRecord, ThemeLet]) -> tuple[int, str]:
            top = pair[0].path[0]
            return (-1 - rank[top] if top in rank else 0, pair[0].qualname)

        by_name: dict[str, list[ModuleRecord]] = {}
        lets: dict[tuple[str, str], ThemeLet] = {}
        for record, let in sorted(self._kernel.lets(ThemeLet), key=order):
            by_name.setdefault(let.name, []).append(record)
            lets[record.qualname, let.name] = let
        themes = {DEFAULT_THEME.name: Offered(DEFAULT_THEME, None, logos=DEFAULT_LOGO)}
        conflicts: list[str] = []
        for name, records in by_name.items():
            winner, *others = records
            branch = _branch(stack, winner.path[0]) if winner.path[0] in rank else None
            rivals = [
                r.path[0] for r in others if r.path[0] in rank and _branch(stack, r.path[0]) not in {None, branch}
            ]
            if branch is not None and rivals:
                conflicts.append(
                    f"{name}: offered by {winner.path[0]} and {', '.join(rivals)}, an org and the site; nobody "
                    f"decided which applies, so neither does until {stack.layer or 'your team'} offers it"
                )
                continue
            let = lets[winner.qualname, name]
            themes[name] = Offered(let.theme, winner, [r.qualname for r in others], let, let.logos())
        return Available(themes, conflicts)

    def listing(self) -> ThemeList:
        """Every available theme, the one in effect, and why it differs from the one configured.

        Raises:
            HeinzelError: If a configuration file is not valid TOML.
        """
        # Read now, not from the settings of boot: `hz ui` keeps running while a terminal switches the theme.
        config = Config.load()
        available = self.available()
        note, next_steps = None, list[str]()
        try:
            configured = config.settings(ThemeSettings, SECTION).name
        except ValidationError:
            configured, note = (
                DEFAULT_THEME.name,
                (
                    f"`[system.theme] name` (or {VARIABLE}) isn't a theme name: lowercase words joined by dashes; the "
                    "default is in effect."
                ),
            )
            next_steps = [f"hz system theme use {DEFAULT_THEME.name}"]
        selected = configured
        if configured not in available.themes:
            selected = DEFAULT_THEME.name
            note = (
                f"No installed plugin offers the theme {configured!r}; the default is in effect until you pick "
                "another or install its plugin."
            )
            next_steps = [f"hz system theme use {DEFAULT_THEME.name}"]
        if note is None and configured != self._from_files(config):
            note = f"{VARIABLE} in your environment decides the theme ({configured}); unset it to switch."
            next_steps = [f"unset {VARIABLE}"]
        if not next_steps:
            next_steps = [f"hz system theme use {name}" for name in available.themes if name != selected][:1]
        return ThemeList(
            selected=selected,
            themes=[
                ThemeView(
                    name=name,
                    title=offered.theme.title,
                    module=offered.record.qualname if offered.record else None,
                    plugin=offered.record.origin.distribution if offered.record else None,
                    selected=name == selected,
                    light=offered.theme.light,
                    dark=offered.theme.dark,
                    font_sans=offered.theme.font_sans,
                    font_mono=offered.theme.font_mono,
                    radius=offered.theme.radius,
                    radius_control=offered.theme.radius_control,
                    border_width=offered.theme.border_width,
                    font_size=offered.theme.font_size,
                    font_size_small=offered.theme.font_size_small,
                    icon_size=offered.theme.icon_size,
                    row_height=offered.theme.row_height,
                    heading_weight=offered.theme.heading_weight,
                    heading_tracking=offered.theme.heading_tracking,
                    spacing=offered.theme.spacing,
                    fonts=_fonts(offered),
                    logo=_logo(offered.logos),
                    variant=list(offered.theme.variant),
                    heinzel=offered.theme.heinzel.model_copy(update={"assets": {}}) if offered.theme.heinzel else None,
                    heinzel_assets=offered.let.avatar_urls() if offered.let else {},
                    shadows=offered.shadows,
                )
                for name, offered in available.themes.items()
            ],
            conflicts=available.conflicts,
            note=note,
            next=next_steps,
        )

    def selected(self) -> str:
        """The name of the theme in effect, after every fallback; what `system.sound` follows (ADR 0058).

        Raises:
            HeinzelError: If a configuration file is not valid TOML.
        """
        return self.listing().selected

    def font(self, theme: str, index: int) -> bytes | None:
        """The content of font `index` of the available theme `theme` (the one that wins its name), or None."""
        offered = self.available().themes.get(theme)
        if offered is None or offered.let is None:
            return None
        try:
            return offered.let.font(index)
        except ValueError:
            return None

    def logo(self, theme: str, name: str) -> bytes | None:
        """The content of logo `name` of the available theme `theme` (the one that wins its name), or None."""
        offered = self.available().themes.get(theme)
        if offered is None:
            return None
        try:
            if name.startswith("heinzel-") and offered.let:
                return offered.let.avatar(name.removeprefix("heinzel-"))
            return offered.logos.content(name) if offered.logos else None
        except ValueError:
            return None

    def use(self, name: str) -> ThemeList:
        """Write `name` as the theme to the user configuration file, then list the themes.

        Raises:
            HeinzelError: If no installed plugin offers a theme of that name, or the file can't be written.
        """
        themes = self.available().themes
        if name not in themes:
            msg = (
                f"No theme is called {name!r}; available: {', '.join(themes)}. Pick one, e.g. "
                f"`hz system theme use {DEFAULT_THEME.name}`, or install the plugin that offers it."
            )
            raise HeinzelError(msg)
        self._editor.write(KEY, name, "user")
        return self.listing()

    def signals(self) -> ThemeSignalsView:
        """The signals now (ADR 0060): the daypart in the person's time zone, and what the modules publish.

        Each module's signals operation runs at most once per its interval, on a worker of its own with a deadline of
        `DEADLINE`: one that fails, hangs or returns something else leaves its signals unknown until the next
        interval, and never holds up the answer. Of several modules publishing one signal, the first by module order
        wins; sunrise and sunset come as a pair from the first module that publishes both.
        """
        zone, zone_name = _zone()
        now = utc_now()
        weather: Weather | None = None
        sun: tuple[datetime, datetime] | None = None
        sources: list[str] = []
        for record, let in self._kernel.lets(SignalLet):
            published = self._published(record.qualname, let, now)
            if published is None:
                continue
            contributed = False
            if weather is None and published.weather is not None:
                weather, contributed = published.weather, True
            if sun is None and published.sunrise is not None and published.sunset is not None:
                sun, contributed = (published.sunrise, published.sunset), True
            if contributed:
                sources.append(record.qualname)
        sunrise, sunset = (sun[0].astimezone(zone), sun[1].astimezone(zone)) if sun else (None, None)
        return ThemeSignalsView(
            daypart=daypart(now.astimezone(zone), sunrise, sunset),
            weather=weather,
            sunrise=sunrise,
            sunset=sunset,
            timezone=zone_name,
            sources=sources,
            refresh=int(REFRESH.total_seconds()),
        )

    def _published(self, qualname: str, let: SignalLet, now: datetime) -> ThemeSignals | None:
        """What `let` of module `qualname` publishes, read again once its interval has passed.

        One caller reads at a time per service; the attempt is recorded before the call, so callers meanwhile and
        after a failure get the last values (or unknown) instead of calling again.
        """
        key = f"{qualname}:{let.name}"
        with self._lock:
            cached = self._read.get(key)
            if cached and now - cached[0] < let.every:
                return cached[1]
            self._read[key] = (now, cached[1] if cached else None)
        operation = next((op for _, op in self._kernel.lets(OperationLet) if op.func is let.func), None)
        if operation is None:
            logger.warning("Signals %s have no operation; they stay unknown", key)
            return None
        published: ThemeSignals | None = None
        try:
            result = self._workers.submit(self._kernel.invoke, operation).result(timeout=DEADLINE.total_seconds())
            published = ThemeSignals.model_validate(result.model_dump() if isinstance(result, ThemeSignals) else result)
        except FutureTimeout:
            logger.warning("Signals %s took longer than %s; they stay unknown until %s", key, DEADLINE, let.every)
        except ValidationError as exc:
            logger.warning("Signals %s aren't ThemeSignals (%s); they stay unknown until %s", key, exc, let.every)
        except Exception:
            logger.warning("Signals %s failed; they stay unknown until %s", key, let.every, exc_info=True)
        with self._lock:
            self._read[key] = (now, published)
        return published

    @staticmethod
    def _from_files(config: Config) -> str:
        """The name the configuration files set, a project file aside (it can't set it); the default otherwise."""
        name = DEFAULT_THEME.name
        for file, content in config.layers:
            if file.scope != "project":
                name = str(Config(files=(), data=content).section(SECTION).get("name", name))
        return name


def utc_now() -> datetime:
    """Now, aware, in UTC; a seam tests replace."""
    return datetime.now(UTC)


def _zone() -> tuple[tzinfo, str]:
    """The person's time zone (`[org] timezone`, which a project file can't set), else this machine's, and its name.

    A name that isn't an IANA time zone is ignored, as `hz setup` would refuse it.
    """
    name: object = os.environ.get("HZ_ORG__TIMEZONE")
    if name is None:
        try:
            layers = Config.load().layers
        except HeinzelError:
            layers = ()
        for file, content in layers:
            if file.scope != "project":
                name = Config(files=(), data=content).section("org").get("timezone", name)
    for candidate in (name, local_timezone()):
        if isinstance(candidate, str) and candidate in available_timezones():
            return ZoneInfo(candidate), candidate
    return ZoneInfo("UTC"), "UTC"


def local_timezone() -> str | None:
    """The IANA name of this machine's time zone, from where /etc/localtime points; a seam tests replace."""
    try:
        target = str(LOCALTIME.resolve(strict=True))
    except OSError:
        return None
    _, found, zone = target.partition("zoneinfo/")
    return zone or None if found else None
