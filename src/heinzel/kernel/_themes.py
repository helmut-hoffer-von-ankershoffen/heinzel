"""Themes: how the web app looks, as data a plugin ships (ADR 0044).

A theme is a name, a title, the colours of the web app's tokens for light and dark, two font stacks, a corner radius
and a border width. Every value ends up as a CSS custom property, so each is checked against a grammar that can't
leave its declaration: hex colours, a CSS length, and font stacks of family names from a small character set. Fonts
are those installed on the person's machine: the web head's CSP (`font-src 'self'`) loads no remote font.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from heinzel.kernel._lets import Let

THEME_SUFFIX = ".toml"
DEFAULT = "default"
"""The built-in theme's name: always there, never redeclared by a plugin."""
MAX_NAME = 64
MAX_TITLE = 64
MAX_FONT_STACK = 300
NAME = r"^[a-z](?:-?[a-z0-9])*$"
HEX = r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$"
LENGTH = r"^(?:0|(?:[0-9]{1,3}(?:\.[0-9]{1,4})?|\.[0-9]{1,4})(?:px|rem|em))$"
"""A CSS length: `0`, or a number with `px`, `rem` or `em`, e.g. `0.5rem` or `1px`; no `calc()`, no `var()`."""
_FAMILY = re.compile(r'^(?:-?[A-Za-z][A-Za-z0-9-]*(?: [A-Za-z][A-Za-z0-9-]*)*|"[A-Za-z0-9][A-Za-z0-9 ._-]*")$')
"""One family in a stack: unquoted identifiers (`system-ui`, `-apple-system`) or a double-quoted name (`"SF Mono"`)."""
CSS_WIDE = frozenset({"inherit", "initial", "unset", "revert", "revert-layer", "default"})
"""Keywords CSS reserves; unquoted inside a stack they make the whole declaration invalid."""
_RULES = {
    "string_pattern_mismatch": {
        "name": "lowercase words joined by single dashes, e.g. acme",
        "radius": "a CSS length: 0, or a number with px, rem or em, e.g. 0.75rem",
        "border_width": "a CSS length: 0, or a number with px, rem or em, e.g. 1px",
    },
}

Colour = Annotated[
    str, StringConstraints(pattern=HEX, to_lower=True), Field(description="A hex colour, `#rgb` or `#rrggbb`.")
]
Length = Annotated[str, Field(pattern=LENGTH, description="A CSS length: `0`, or a number with px, rem or em.")]


def font_stack_problem(stack: str) -> str | None:
    """Why `stack` isn't a safe font-family stack, or None.

    A stack is printable ASCII: family names separated by commas. A name is unquoted words that start with a letter
    (one leading dash allowed) and hold letters, digits and dashes, or a double-quoted name of letters, digits,
    spaces, dots, underscores and dashes. Nothing else passes: no `;`, `}`, `(` (so no `url(` or `var(`), no
    backslash, no single quote, no quote inside a name, no newline, and no CSS-wide keyword such as `inherit`.
    """
    if not (stack.isascii() and stack.isprintable()):
        return "a font stack is one line of printable ASCII characters"
    if not stack.strip():
        return "a font stack names at least one family, e.g. system-ui, sans-serif"
    if len(stack) > MAX_FONT_STACK:
        return f"a font stack is at most {MAX_FONT_STACK} characters"
    for family in stack.split(","):
        if family.strip().lower() in CSS_WIDE:
            return f"{family.strip()!r} is a CSS keyword, not a font family; quote it if a font has that name"
        if not _FAMILY.fullmatch(family.strip()):
            return (
                f"{family.strip()!r} isn't a font family name: use letters, digits, spaces and dashes, and double "
                'quotes around a name with other characters, e.g. "SF Mono"'
            )
    return None


def _font_stack(value: str) -> str:
    if problem := font_stack_problem(value):
        raise ValueError(problem)
    return ", ".join(family.strip() for family in value.split(","))


def _one_line(value: str) -> str:
    if any(not character.isprintable() for character in value):
        msg = "the title is one line of printable characters"
        raise ValueError(msg)
    return value


FontStack = Annotated[str, AfterValidator(_font_stack)]


class ThemeColours(BaseModel):
    """The colours of the web app's tokens in one mode; each becomes `--color-<token>` (underscores as dashes)."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    background: Colour
    foreground: Colour
    muted: Colour
    muted_foreground: Colour
    card: Colour
    border: Colour
    primary: Colour
    primary_foreground: Colour
    accent: Colour
    ok: Colour
    warn: Colour
    bad: Colour
    ring: Colour
    sidebar: Colour
    input: Colour
    popover: Colour


class Theme(BaseModel):
    """A look for the web app: colours for light and dark, fonts, corner radius and border width (ADR 0044)."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    name: str = Field(pattern=NAME, max_length=MAX_NAME, description="Lowercase words joined by dashes, e.g. `acme`.")
    title: Annotated[str, AfterValidator(_one_line)] = Field(
        min_length=1, max_length=MAX_TITLE, description="What people pick it by, e.g. `Acme`."
    )
    light: ThemeColours = Field(description="The colours in light mode.")
    dark: ThemeColours = Field(description="The colours in dark mode.")
    font_sans: FontStack = Field(
        description="The font-family stack of text, installed fonts only, e.g. `system-ui, sans-serif`."
    )
    font_mono: FontStack = Field(
        description="The font-family stack of code, installed fonts only, e.g. `ui-monospace, monospace`."
    )
    radius: Length = Field(description="The corner radius of cards, inputs and buttons, e.g. `0.5rem`.")
    border_width: Length = Field(description="The width of borders, e.g. `1px`.")


@dataclass(frozen=True, kw_only=True, slots=True)
class ThemeLet(Let):
    """A theme a module offers (`module.theme`), named after it; consumed by `system.theme`."""

    theme: Theme

    def __post_init__(self) -> None:
        """Check the theme again, so one built with `model_construct` can't skip the grammar.

        Raises:
            ValueError: If the theme isn't valid, is called `default`, or the let isn't named after it.
        """
        try:
            theme = Theme.model_validate(self.theme.model_dump())
        except ValidationError as exc:
            msg = f"Theme {self.theme.name!r} isn't valid: {problems_of(exc)}"
            raise ValueError(msg) from exc
        if theme.name == DEFAULT:
            msg = f"No plugin can offer a theme named {DEFAULT!r}: that is the built-in one"
            raise ValueError(msg)
        if self.name != theme.name:
            msg = f"A theme let is named after its theme: {theme.name!r}, not {self.name!r}"
            raise ValueError(msg)
        object.__setattr__(self, "theme", theme)


def problems_of(exc: ValidationError) -> str:
    """Each problem of a theme as `field: rule`, in words a person editing a theme file reads."""
    problems: list[str] = []
    for error in exc.errors():
        where = ".".join(map(str, error["loc"])) or "theme"
        field = str(error["loc"][-1]) if error["loc"] else ""
        if error["type"] == "string_pattern_mismatch":
            rule = _RULES["string_pattern_mismatch"].get(field, "a hex colour, #rgb or #rrggbb, e.g. #0f766e")
        else:
            rule = error["msg"].removeprefix("Value error, ")
        problems.append(f"{where}: {rule}")
    return "; ".join(problems)


def themes_in(source: Path | Theme) -> tuple[Theme, ...]:
    """The themes of `source`: a `Theme`, a `*.toml` file of one, or a directory of them, read now.

    Raises:
        ValueError: If a file isn't a valid theme, or a directory holds none.
        OSError: If a file can't be read.
    """
    if isinstance(source, Theme):
        return (source,)
    files = sorted(source.glob(f"*{THEME_SUFFIX}")) if source.is_dir() else [source]
    if not files:
        msg = f"{source} holds no theme files (*{THEME_SUFFIX})"
        raise ValueError(msg)
    themes: list[Theme] = []
    for file in files:
        if file.suffix != THEME_SUFFIX:
            msg = f"Theme file {file} must end in {THEME_SUFFIX}"
            raise ValueError(msg)
        try:
            themes.append(Theme.model_validate(tomllib.loads(file.read_text(encoding="utf-8"))))
        except tomllib.TOMLDecodeError as exc:
            msg = f"Theme file {file} isn't valid TOML: {exc}"
            raise ValueError(msg) from exc
        except ValidationError as exc:
            msg = f"Theme file {file} isn't a valid theme: {problems_of(exc)}"
            raise ValueError(msg) from exc
    return tuple(themes)


DEFAULT_THEME = Theme(
    name=DEFAULT,
    title="Default",
    # The web app's own palette (web/src/index.css), its oklch values as hex; sidebar and popover follow muted and
    # card, input follows border.
    light=ThemeColours(
        background="#fdfcf9",
        foreground="#221811",
        muted="#f3f0ea",
        muted_foreground="#61564d",
        card="#ffffff",
        border="#e1ddd7",
        primary="#af4000",
        primary_foreground="#fdfcf9",
        accent="#f9e8d6",
        ok="#137738",
        warn="#9d6400",
        bad="#be222a",
        ring="#af4000",
        sidebar="#f3f0ea",
        input="#e1ddd7",
        popover="#ffffff",
    ),
    dark=ThemeColours(
        background="#16100c",
        foreground="#ebe7e1",
        muted="#241e1a",
        muted_foreground="#aba39b",
        card="#1d1713",
        border="#38322d",
        primary="#e78a45",
        primary_foreground="#16100c",
        accent="#37281c",
        ok="#62c37a",
        warn="#e9b452",
        bad="#f97770",
        ring="#e78a45",
        sidebar="#241e1a",
        input="#38322d",
        popover="#1d1713",
    ),
    font_sans='ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif',
    font_mono='ui-monospace, "SF Mono", Menlo, monospace',
    radius="0.5rem",
    border_width="1px",
)
"""The built-in theme: always available, the one in effect until the person picks another."""
