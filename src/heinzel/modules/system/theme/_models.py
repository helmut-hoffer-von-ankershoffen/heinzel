"""Models and settings of the theme module."""

from typing import ClassVar

from pydantic import BaseModel, Field

from heinzel import ModuleSettings, ThemeColours

NAME_PATTERN = r"^[a-z](?:-?[a-z0-9])*$"


class ThemeSettings(ModuleSettings):
    """Which theme the web app uses: the `[system.theme]` table or `HZ_SYSTEM_THEME__NAME`.

    User scope: a project `hz.toml` can't set it, because a repository someone else controls must not decide how a
    person's tool looks.
    """

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"name"})

    name: str = Field(
        default="default",
        pattern=NAME_PATTERN,
        max_length=64,
        description="The theme of the web app, one `hz system theme list` shows, e.g. `acme`; `default` is built in.",
    )


class ThemeView(BaseModel):
    """One available theme with every token value, ready to become the web app's CSS custom properties."""

    name: str = Field(description="What `hz system theme use` takes, e.g. `acme`.")
    title: str = Field(description="What people pick it by, e.g. `Acme`.")
    module: str | None = Field(description="The qualified name of the module offering it; none for the built-in one.")
    plugin: str | None = Field(description="The plugin it comes with, e.g. `heinzel-acme`; none for the built-in one.")
    selected: bool = Field(description="Whether it is the theme in effect.")
    light: ThemeColours = Field(description="Colours in light mode; each field is the token `--color-<field>`.")
    dark: ThemeColours = Field(description="Colours in dark mode; each field is the token `--color-<field>`.")
    font_sans: str = Field(description="The font-family stack of text (`--font-sans`), installed fonts only.")
    font_mono: str = Field(description="The font-family stack of code (`--font-mono`), installed fonts only.")
    radius: str = Field(description="The corner radius (`--radius`), a CSS length, e.g. `0.5rem`.")
    border_width: str = Field(description="The width of borders (`--border-width`), a CSS length, e.g. `1px`.")
    shadows: list[str] = Field(
        default_factory=list[str],
        description="Modules that offer a theme of the same name, hidden by this one (layer order decides).",
    )


class ThemeList(BaseModel):
    """Every available theme and which one is in effect."""

    selected: str = Field(description="The name of the theme in effect.")
    themes: list[ThemeView] = Field(description="The built-in `default` first, then the plugins' themes.")
    conflicts: list[str] = Field(
        default_factory=list[str],
        description="Names an org and the site both offer with nothing more specific deciding; left out until one is.",
    )
    note: str | None = Field(default=None, description="Why the theme in effect isn't the one configured.")
    next: list[str] = Field(default_factory=list[str], description="What to run next.")
