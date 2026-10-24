"""The theme module of system: how the web app looks (ADR 0044).

A theme is data a plugin offers with `module.theme(...)`: colours for light and dark, fonts and font files, a logo,
type scale, density, corner radii and border width. `default` is built in. The web app reads `hz system theme list`
and applies the selected theme's values as CSS custom properties; `hz system theme use <name>` picks one for the person.
"""

from typing import Annotated

from pydantic import Field
from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from heinzel import Module, ThemeChoice, ThemeFonts
from heinzel.modules.system.theme._models import (
    ThemeFontView,
    ThemeList,
    ThemeLogoView,
    ThemeSettings,
    ThemeView,
)
from heinzel.modules.system.theme._service import ThemeService

module = Module("theme", "Pick how the web app looks: the built-in theme or one a plugin offers.")
module.settings(ThemeSettings)
module.service(ThemeService)
module.service(ThemeFonts, ThemeService)  # the web head serves the themes' fonts and logos through it (ADR 0044)
module.service(ThemeChoice, ThemeService)  # system.sound follows the theme in effect through it (ADR 0058)


def _swatch(view: ThemeView) -> Text:
    """The primary colour on the background, light then dark, in the theme's own colours."""
    swatch = Text()
    for colours in (view.light, view.dark):
        swatch.append("██", style=f"{colours.primary} on {colours.background}")
        swatch.append(" ")
    return swatch


def render(result: ThemeList) -> RenderableType:
    """The themes with the one in effect marked, where each comes from, and what to run next."""
    table = Table(box=None, pad_edge=False)
    for column in ("", "Theme", "Title", "From", "Colours"):
        table.add_column(column, overflow="fold")
    for view in result.themes:
        table.add_row(
            "*" if view.selected else "",
            Text(view.name, style="bold" if view.selected else ""),
            Text(view.title),
            Text(view.plugin or "built in"),
            _swatch(view),
        )
    parts: list[RenderableType] = [table]
    if shadowed := [f"{v.name} hides the one in {', '.join(v.shadows)}" for v in result.themes if v.shadows]:
        parts.append(Text("Same name in several plugins, the more specific layer wins: " + "; ".join(shadowed)))
    parts += [Text(f"Left out: {conflict}", style="yellow") for conflict in result.conflicts]
    if result.note:
        parts.append(Text(result.note, style="yellow"))
    if result.next:
        parts.append(Text("Next:\n" + "\n".join(f"  {step}" for step in result.next)))
    return Group(*parts)


@module.operation(name="list", render=render)
def list_(service: ThemeService) -> ThemeList:
    """List the themes of the web app, with every colour, font and size, and which one is in effect."""
    return service.listing()


@module.operation(read_only=False, render=render)
def use(
    service: ThemeService,
    name: Annotated[str, Field(description="The theme to use, one `hz system theme list` shows, e.g. `default`.")],
) -> ThemeList:
    """Use a theme in the web app: writes `[system.theme] name` to your configuration file."""
    return service.use(name)


__all__ = ["ThemeFontView", "ThemeList", "ThemeLogoView", "ThemeService", "ThemeSettings", "ThemeView", "module"]
