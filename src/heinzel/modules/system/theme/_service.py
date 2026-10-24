"""Service of the theme module: the themes plugins offer, ties decided by layer order, and `[system.theme] name`."""

from dataclasses import dataclass, field

from pydantic import ValidationError

from heinzel import (
    Config,
    ConfigEditor,
    HeinzelError,
    Kernel,
    LayerStack,
    ModuleRecord,
    Theme,
    ThemeFonts,
    ThemeLet,
)
from heinzel.kernel import DEFAULT_LOGO, DEFAULT_THEME, ThemeLogoFiles
from heinzel.modules.system.theme._models import ThemeFontView, ThemeList, ThemeLogoView, ThemeSettings, ThemeView

SECTION = "system.theme"
KEY = f"{SECTION}.name"
VARIABLE = "HZ_SYSTEM_THEME__NAME"


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


class ThemeService(ThemeFonts):
    """The themes of the web app (ADR 0044): the built-in one and every theme the installed plugins offer."""

    def __init__(self, kernel: Kernel, editor: ConfigEditor) -> None:
        """Create the service; the kernel and the configuration editor are injected."""
        self._kernel = kernel
        self._editor = editor

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
                    shadows=offered.shadows,
                )
                for name, offered in available.themes.items()
            ],
            conflicts=available.conflicts,
            note=note,
            next=next_steps,
        )

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
        if offered is None or offered.logos is None:
            return None
        try:
            return offered.logos.content(name)
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

    @staticmethod
    def _from_files(config: Config) -> str:
        """The name the configuration files set, a project file aside (it can't set it); the default otherwise."""
        name = DEFAULT_THEME.name
        for file, content in config.layers:
            if file.scope != "project":
                name = str(Config(files=(), data=content).section(SECTION).get("name", name))
        return name
