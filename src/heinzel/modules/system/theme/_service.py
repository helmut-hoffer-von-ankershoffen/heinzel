"""Service of the theme module: the themes plugins offer, ties decided by layer order, and `[system.theme] name`."""

import os

from heinzel import Config, ConfigEditor, HeinzelError, Kernel, ModuleRecord, Theme, ThemeLet
from heinzel.kernel import DEFAULT_THEME
from heinzel.modules.system.theme._models import ThemeList, ThemeSettings, ThemeView

SECTION = "system.theme"
KEY = f"{SECTION}.name"
VARIABLE = "HZ_SYSTEM_THEME__NAME"


class ThemeService:
    """The themes of the web app (ADR 0044): the built-in one and every theme the installed plugins offer."""

    def __init__(self, kernel: Kernel, editor: ConfigEditor) -> None:
        """Create the service; the kernel and the configuration editor are injected."""
        self._kernel = kernel
        self._editor = editor

    def available(self) -> dict[str, tuple[Theme, ModuleRecord | None, list[str]]]:
        """Each theme by name, with the module offering it and the modules whose same-named theme it hides.

        Of two modules offering a theme of the same name, the more specific layer of the person's stack wins
        (ADR 0040), then a layer in the stack over a module outside it, then the first module by qualified name.
        """
        rank = {name: index for index, name in enumerate(self._kernel.layer_stack().names)}

        def order(pair: tuple[ModuleRecord, ThemeLet]) -> tuple[int, str]:
            top = pair[0].path[0]
            return (-1 - rank[top] if top in rank else 0, pair[0].qualname)

        found: dict[str, tuple[Theme, ModuleRecord | None, list[str]]] = {DEFAULT_THEME.name: (DEFAULT_THEME, None, [])}
        for record, let in sorted(self._kernel.lets(ThemeLet), key=order):
            if let.name in found:
                found[let.name][2].append(record.qualname)
            else:
                found[let.name] = (let.theme, record, [])
        return found

    def listing(self) -> ThemeList:
        """Every available theme, the one in effect, and why it differs from the one configured.

        Raises:
            HeinzelError: If a configuration file is not valid TOML.
        """
        # Read now, not from the settings of boot: `hz ui` keeps running while a terminal switches the theme.
        configured = Config.load().settings(ThemeSettings, SECTION).name
        themes = self.available()
        selected, note, next_steps = configured, None, list[str]()
        if configured not in themes:
            selected = DEFAULT_THEME.name
            note = (
                f"No installed plugin offers the theme {configured!r}; the default is in effect until you pick "
                "another or install its plugin."
            )
            next_steps = [f"hz system theme use {DEFAULT_THEME.name}"]
        if variable := next((name for name in os.environ if name.upper() == VARIABLE), None):
            note = f"{variable}={os.environ[variable]} in your environment decides the theme; unset it to switch."
            next_steps = [f"unset {variable}"]
        if not next_steps:
            next_steps = [f"hz system theme use {name}" for name in themes if name != selected][:1]
        return ThemeList(
            selected=selected,
            themes=[
                ThemeView(
                    name=name,
                    title=theme.title,
                    module=record.qualname if record else None,
                    plugin=record.origin.distribution if record else None,
                    selected=name == selected,
                    light=theme.light,
                    dark=theme.dark,
                    font_sans=theme.font_sans,
                    font_mono=theme.font_mono,
                    radius=theme.radius,
                    border_width=theme.border_width,
                    shadows=shadows,
                )
                for name, (theme, record, shadows) in themes.items()
            ],
            note=note,
            next=next_steps,
        )

    def use(self, name: str) -> ThemeList:
        """Write `name` as the theme to the user configuration file, then list the themes.

        Raises:
            HeinzelError: If no installed plugin offers a theme of that name, or the file can't be written.
        """
        themes = self.available()
        if name not in themes:
            msg = (
                f"No theme is called {name!r}; available: {', '.join(themes)}. Pick one, e.g. "
                f"`hz system theme use {DEFAULT_THEME.name}`, or install the plugin that offers it."
            )
            raise HeinzelError(msg)
        self._editor.write(KEY, name, "user")
        return self.listing()
