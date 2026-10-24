"""Themes: how the web app looks, as data a plugin ships (ADR 0044).

A theme is a name, a title, the colours of the web app's tokens for light and dark, two font stacks, its type scale,
density and corner radii, a border width, and optionally font files of its own. Every value ends up as a CSS custom
property, so each is checked against a grammar that can't leave its declaration: hex colours, a CSS length, an integer
weight, and font stacks of family names from a small character set. A font file is a `.woff2` inside the theme's own
directory, checked at import and again when the web head serves it from its own origin: the web head's CSP
(`font-src 'self'`) loads no remote font. A logo is SVG files in the same directory, held to the same path rules and
refused if they could run anything or load anything from outside themselves.
"""

import hashlib
import os
import re
import stat
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from heinzel.kernel._lets import Let
from heinzel.kernel._theme_signals import Daypart, Weather

THEME_SUFFIX = ".toml"
DEFAULT = "default"
"""The built-in theme's name: always there, never redeclared by a plugin."""
MAX_NAME = 64
MAX_TITLE = 64
MAX_FONT_STACK = 300
NAME = r"^[a-z](?:-?[a-z0-9])*$"
HEX = r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$"
_MAGNITUDE = r"(?:[0-9]{1,3}(?:\.[0-9]{1,4})?|\.[0-9]{1,4})(?:px|rem|em)"
LENGTH = rf"^(?:0|{_MAGNITUDE})$"
"""A CSS length: `0`, or a number with `px`, `rem` or `em`, e.g. `0.5rem` or `1px`; no `calc()`, no `var()`."""
SIGNED_LENGTH = rf"^(?:0|-?{_MAGNITUDE})$"
"""A CSS length that may be negative, for letter spacing: `0`, `-0.5px`, `0.01em`."""
_FAMILY = re.compile(r'^(?:-?[A-Za-z][A-Za-z0-9-]*(?: [A-Za-z][A-Za-z0-9-]*)*|"[A-Za-z0-9][A-Za-z0-9 ._-]*")$')
"""One family in a stack: unquoted identifiers (`system-ui`, `-apple-system`) or a double-quoted name (`"SF Mono"`)."""
CSS_WIDE = frozenset({"inherit", "initial", "unset", "revert", "revert-layer", "default"})
"""Keywords CSS reserves; unquoted inside a stack they make the whole declaration invalid."""
FAMILY = r"^[A-Za-z0-9][A-Za-z0-9._-]*(?: [A-Za-z0-9._-]+)*$"
"""The family a theme's font file provides: what a double-quoted name in a stack may hold, e.g. `Acme Sans`."""
MAX_FAMILY = 64
FONT_FILE = r"^(?:[A-Za-z0-9_][A-Za-z0-9._-]*/){0,7}[A-Za-z0-9_][A-Za-z0-9._-]*\.woff2$"
"""A relative path of plain names inside the theme's directory ending in `.woff2`: no `..`, no leading `/` or `.`."""
MAX_FONT_FILE = 200
MAX_FONTS = 16
MAX_FONT_BYTES = 2 * 1024 * 1024
"""The largest font file a theme may ship, 2 MB."""
WOFF2_SIGNATURE = b"wOF2"
WEIGHT_MIN, WEIGHT_MAX = 100, 900
FONT_ROUTE = "/theme-fonts"
"""Where the web head serves themes' font files, outside `/api`: `@font-face` and `FontFace` send no session token."""
LOGO_FILE = r"^(?:[A-Za-z0-9_][A-Za-z0-9._-]*/){0,7}[A-Za-z0-9_][A-Za-z0-9._-]*\.svg$"
"""A relative path of plain names inside the theme's directory ending in `.svg`: no `..`, no leading `/` or `.`."""
MAX_LOGO_BYTES = 200 * 1024
"""The largest logo file a theme may ship, 200 KB."""
MAX_ALT = 64
LOGO_ROUTE = "/theme-assets"
"""Where the web head serves themes' logos, outside `/api`: an `<img>` sends no session token."""
LOGO_NAMES = ("light", "dark", "mark")
"""The logo files a theme can have, by the name the web head serves them under: `<name>.svg`."""
LOGO_CSP = "default-src 'none'; style-src 'unsafe-inline'"
"""The Content Security Policy of a served logo: opened on its own, it loads nothing and runs nothing."""
MAX_STOPS = 4
MAX_BACKDROP_OPACITY = 0.9
"""A backdrop never covers `background` entirely, and the contrast check decides the rest."""
MAX_VARIANTS = 32
MIN_CONTRAST = 4.5
"""The WCAG AA contrast of body text, checked for the base and every variant in both modes."""
_SVG = "{http://www.w3.org/2000/svg}"
_REFERENCES = frozenset({"href", "src"})
_LOGO_PATH_RULE = (
    "a path inside the theme's directory of letters, digits, dots, underscores and dashes, ending in .svg, e.g. "
)
_LENGTH_RULE = "a CSS length: 0, or a number with px, rem or em, e.g. "
_RULES = {
    "string_pattern_mismatch": {
        "name": "lowercase words joined by single dashes, e.g. acme",
        "radius": _LENGTH_RULE + "0.75rem",
        "radius_control": _LENGTH_RULE + "0.5rem",
        "border_width": _LENGTH_RULE + "1px",
        "font_size": _LENGTH_RULE + "15px",
        "font_size_small": _LENGTH_RULE + "13px",
        "icon_size": _LENGTH_RULE + "18px",
        "row_height": _LENGTH_RULE + "36px",
        "spacing": _LENGTH_RULE + "0.25rem",
        "heading_tracking": "a CSS length that may be negative: 0, or a number with px, rem or em, e.g. -0.5px",
        "family": "letters, digits, single spaces, dots, underscores and dashes, e.g. Acme Sans",
        "file": (
            "a path inside the theme's directory of letters, digits, dots, underscores and dashes, ending in .woff2, "
            "e.g. fonts/acme-sans.woff2"
        ),
        "weight": "one weight or a range of two, 100 to 900, e.g. 400 or 100 900",
        "light": _LOGO_PATH_RULE + "logo/wordmark-light.svg",
        "dark": _LOGO_PATH_RULE + "logo/wordmark-dark.svg",
        "mark": _LOGO_PATH_RULE + "logo/mark.svg",
        "height": _LENGTH_RULE + "24px",
    },
}

Colour = Annotated[
    str, StringConstraints(pattern=HEX, to_lower=True), Field(description="A hex colour, `#rgb` or `#rrggbb`.")
]
Length = Annotated[str, Field(pattern=LENGTH, description="A CSS length: `0`, or a number with px, rem or em.")]
SignedLength = Annotated[
    str, Field(pattern=SIGNED_LENGTH, description="A CSS length that may be negative, e.g. `-0.5px` or `0`.")
]


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


def _one_line_alt(value: str) -> str:
    if any(not character.isprintable() for character in value):
        msg = "the alt text is one line of printable characters"
        raise ValueError(msg)
    return value


def _weight_range(value: str) -> str:
    weights = [int(part) for part in value.split(" ")]
    if any(not WEIGHT_MIN <= weight <= WEIGHT_MAX for weight in weights) or weights != sorted(weights):
        msg = f"a weight is {WEIGHT_MIN} to {WEIGHT_MAX}, and a range goes low to high, e.g. 400 or 100 900"
        raise ValueError(msg)
    return value


FontStack = Annotated[str, AfterValidator(_font_stack)]


class ThemeFont(BaseModel):
    """A font file a theme ships, a `.woff2` inside its theme's directory; the web app loads it with `FontFace`."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    family: str = Field(
        pattern=FAMILY,
        max_length=MAX_FAMILY,
        description="The family name the theme's font stacks use for it, e.g. `Acme Sans`.",
    )
    file: str = Field(
        pattern=FONT_FILE,
        max_length=MAX_FONT_FILE,
        description="The `.woff2` file, relative to the theme file's directory, e.g. `fonts/acme-sans.woff2`.",
    )
    weight: Annotated[str, AfterValidator(_weight_range)] = Field(
        default="400",
        pattern=r"^[0-9]{3}(?: [0-9]{3})?$",
        description="The weight it covers, e.g. `400`, or a variable font's range, e.g. `100 900`.",
    )
    style: Literal["normal", "italic"] = Field(default="normal", description="`normal` or `italic`.")


LogoFile = Annotated[str, Field(pattern=LOGO_FILE, max_length=MAX_FONT_FILE)]


class ThemeLogo(BaseModel):
    """A theme's logo (`[logo]` in its file): SVG files inside its theme's directory, served by the web head."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    light: LogoFile = Field(
        description="The logo on a light background, relative to the theme file's directory, e.g. `logo/light.svg`."
    )
    dark: LogoFile | None = Field(default=None, description="The logo on a dark background; `light` when not given.")
    mark: LogoFile | None = Field(
        default=None, description="A square logo for narrow places such as a collapsed sidebar; none when not given."
    )
    alt: Annotated[str, AfterValidator(_one_line_alt)] = Field(
        min_length=1, max_length=MAX_ALT, description="What the logo says, for screen readers, e.g. `Acme`."
    )
    height: Length = Field(default="24px", description="How tall the logo is shown, e.g. `24px`.")

    def file(self, name: str) -> str | None:
        """The file of logo `name` (`light`, `dark` or `mark`): `dark` falls back to `light`; None if there is none."""
        match name:
            case "light":
                return self.light
            case "dark":
                return self.dark or self.light
            case "mark":
                return self.mark
            case _:
                return None

    def files(self) -> tuple[str, ...]:
        """Every file the logo names, each once."""
        return tuple(dict.fromkeys(file for file in (self.light, self.dark, self.mark) if file))


def _stop(value: str) -> str:
    if re.fullmatch(HEX, value):
        return value.lower()
    if value in COLOUR_TOKENS:
        return value
    msg = f"a stop is a hex colour, e.g. #fde68a, or a colour token of the theme, e.g. primary; not {value[:40]!r}"
    raise ValueError(msg)


Stop = Annotated[str, AfterValidator(_stop)]


class ThemeBackdrop(BaseModel):
    """A gradient behind the main area (`[light.backdrop]`), of hex colours and the theme's own colour tokens.

    The web app builds the CSS from these typed parts: a kind, an angle in degrees and two to four stops, each a hex
    colour or a token name it turns into `var(--color-<token>)`; no other text reaches CSS, and no `url()` exists.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    gradient: Literal["linear", "radial"] = Field(description="`linear` (along `angle`) or `radial` (from the centre).")
    angle: int = Field(
        default=180, ge=0, le=360, strict=True, description="The direction of a linear gradient in degrees, e.g. `180`."
    )
    stops: tuple[Stop, ...] = Field(
        min_length=2,
        max_length=MAX_STOPS,
        description="Two to four colours: hex, e.g. `#fde68a`, or a token of the theme, e.g. `primary`.",
    )
    opacity: float = Field(
        default=0.6,
        ge=0,
        le=MAX_BACKDROP_OPACITY,
        description=f"How strongly it shows over `background`, 0 to {MAX_BACKDROP_OPACITY}, e.g. `0.6`.",
    )


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
    accent_foreground: Colour = Field(
        description="Text and icons of the active navigation item and of links; `foreground` when not given."
    )
    icon: Colour = Field(description="Icons that aren't active; `muted_foreground` when not given.")
    main_tint: Colour | None = Field(
        default=None, description="A tint behind the main content area (`--color-main-tint`); none when not given."
    )
    backdrop: ThemeBackdrop | None = Field(
        default=None, description="A gradient behind the main area only; sidebar and cards stay solid."
    )

    @model_validator(mode="before")
    @classmethod
    def _derived(cls, data: object) -> object:
        """Tokens added later default to one every theme has, so a theme written before them stays valid."""
        if isinstance(data, dict):
            given = dict[object, object](cast("dict[object, object]", data))
            for token, source in (("accent_foreground", "foreground"), ("icon", "muted_foreground")):
                if token not in given and source in given:
                    given[token] = given[source]
            return given
        return data


COLOUR_TOKENS = frozenset(name for name in ThemeColours.model_fields if name != "backdrop")
"""The colour tokens of a theme, which a backdrop's stops may name."""


Override = Annotated[Colour | None, Field(description="A hex colour replacing the base's; unset keeps it.")]


class ThemeOverrides(BaseModel):
    """What a variant changes in one mode: any colour token, the main tint and the backdrop; the rest stays."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    background: Override = None
    foreground: Override = None
    muted: Override = None
    muted_foreground: Override = None
    card: Override = None
    border: Override = None
    primary: Override = None
    primary_foreground: Override = None
    accent: Override = None
    ok: Override = None
    warn: Override = None
    bad: Override = None
    ring: Override = None
    sidebar: Override = None
    input: Override = None
    popover: Override = None
    accent_foreground: Override = None
    icon: Override = None
    main_tint: Override = None
    backdrop: ThemeBackdrop | None = Field(default=None, description="A backdrop that replaces the base's.")


def _words(value: object) -> object:
    """One word or a list of them: `"rain"` is `["rain"]`."""
    return [value] if isinstance(value, str) else value


class ThemeWhen(BaseModel):
    """When a variant applies, e.g. `when = { daypart = "night", weather = ["rain", "drizzle"] }`.

    Every signal it names has one of its values. Signals are a closed vocabulary (ADR 0058), so a misspelt one is
    refused at import.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    daypart: tuple[Daypart, ...] = Field(default=(), max_length=6, description="Any of these dayparts, e.g. `night`.")
    weather: tuple[Weather, ...] = Field(default=(), max_length=10, description="Any of these, e.g. `rain`.")

    @model_validator(mode="before")
    @classmethod
    def _lists(cls, data: object) -> object:
        if isinstance(data, dict):
            given = cast("dict[str, object]", data)
            return {key: _words(value) for key, value in given.items()}
        return data

    @model_validator(mode="after")
    def _some(self) -> "ThemeWhen":
        if not (self.daypart or self.weather):
            msg = 'a variant\'s `when` names at least one signal, e.g. { daypart = "night" }'
            raise ValueError(msg)
        return self

    @property
    def specificity(self) -> int:
        """How many signals it names: the variant naming more of them wins."""
        return bool(self.daypart) + bool(self.weather)

    def matches(self, signals: "Signals") -> bool:
        """Whether every signal it names has one of its values now; an unknown signal matches nothing."""
        return (not self.daypart or signals.get("daypart") in self.daypart) and (
            not self.weather or signals.get("weather") in self.weather
        )


type Signals = dict[str, str | None]
"""Signal values by name, e.g. `{"daypart": "night", "weather": None}`; None when no module provides one."""


class ThemeVariant(BaseModel):
    """A variant of a theme (`[[variant]]`): colours that apply when its signals match (ADR 0058)."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    when: ThemeWhen = Field(description='The signals it follows, e.g. `{ daypart = "night" }`.')
    light: ThemeOverrides | None = Field(default=None, description="What changes in light mode.")
    dark: ThemeOverrides | None = Field(default=None, description="What changes in dark mode.")

    def overrides(self, mode: Literal["light", "dark"]) -> ThemeOverrides | None:
        """What it changes in `mode`."""
        return self.light if mode == "light" else self.dark


def variant_of(variants: tuple[ThemeVariant, ...], signals: Signals) -> int | None:
    """The index of the variant in effect: of those matching, the one naming most signals, the first on a tie."""
    best: int | None = None
    for index, variant in enumerate(variants):
        if variant.when.matches(signals) and (
            best is None or variant.when.specificity > variants[best].when.specificity
        ):
            best = index
    return best


def apply(colours: ThemeColours, overrides: ThemeOverrides | None) -> ThemeColours:
    """`colours` with what `overrides` sets."""
    if overrides is None:
        return colours
    return ThemeColours.model_validate(colours.model_dump() | overrides.model_dump(exclude_none=True))


def _channel(value: int) -> float:
    unit = value / 255
    return unit / 12.92 if unit <= 0.04045 else ((unit + 0.055) / 1.055) ** 2.4  # noqa: PLR2004 - sRGB constants


def _rgb(colour: str) -> tuple[int, int, int]:
    digits = colour.removeprefix("#")
    if len(digits) == 3:  # noqa: PLR2004 - #rgb
        digits = "".join(digit * 2 for digit in digits)
    return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)


def _luminance(rgb: tuple[float, float, float]) -> float:
    red, green, blue = (_channel(round(part)) for part in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(first: str, second: str) -> float:
    """The WCAG contrast ratio of two hex colours, 1 to 21."""
    lighter, darker = sorted((_luminance(_rgb(first)), _luminance(_rgb(second))), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _blend(top: str, bottom: str, opacity: float) -> str:
    opacity = round(opacity * 100) / 100  # as the web app draws it: `color-mix` at a whole percent
    mixed = (t * opacity + b * (1 - opacity) for t, b in zip(_rgb(top), _rgb(bottom), strict=True))
    return "#" + "".join(f"{round(part):02x}" for part in mixed)


def _surfaces(colours: ThemeColours) -> list[tuple[str, str]]:
    """What the main area's text sits on, by name: the background, the tint, each backdrop stop over the background."""
    surfaces = [("background", colours.background)]
    if colours.main_tint:
        surfaces.append(("main_tint", colours.main_tint))
    if backdrop := colours.backdrop:
        for stop in backdrop.stops:
            value = stop if stop.startswith("#") else getattr(colours, stop) or colours.background
            surfaces.append((f"backdrop stop {stop}", _blend(value, colours.background, backdrop.opacity)))
    return surfaces


def contrast_problems(colours: ThemeColours) -> list[str]:
    """Where text in `colours` falls under 4.5:1, in words.

    `foreground` is checked on cards, the sidebar, popovers and every main surface, `muted_foreground` on every main
    surface.
    """
    pairs = [
        ("foreground", surface, colours.foreground, getattr(colours, surface))
        for surface in ("card", "sidebar", "popover")
    ]
    for surface, value in _surfaces(colours):
        pairs += [
            ("foreground", surface, colours.foreground, value),
            ("muted_foreground", surface, colours.muted_foreground, value),
        ]
    return [
        f"{text} on {surface} is {ratio:.2f}:1, under {MIN_CONTRAST}:1"
        for text, surface, first, second in pairs
        if (ratio := contrast(first, second)) < MIN_CONTRAST
    ]


class Theme(BaseModel):
    """A look for the web app: colours, fonts, type scale, density, radii and border width (ADR 0044)."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    name: str = Field(pattern=NAME, max_length=MAX_NAME, description="Lowercase words joined by dashes, e.g. `acme`.")
    title: Annotated[str, AfterValidator(_one_line)] = Field(
        min_length=1, max_length=MAX_TITLE, description="What people pick it by, e.g. `Acme`."
    )
    light: ThemeColours = Field(description="The colours in light mode.")
    dark: ThemeColours = Field(description="The colours in dark mode.")
    font_sans: FontStack = Field(
        description="The font-family stack of text, installed or shipped fonts, e.g. `system-ui, sans-serif`."
    )
    font_mono: FontStack = Field(
        description="The font-family stack of code, installed or shipped fonts, e.g. `ui-monospace, monospace`."
    )
    radius: Length = Field(default="0.75rem", description="The corner radius of cards and dialogs, e.g. `0.75rem`.")
    radius_control: Length = Field(
        default="0.5rem", description="The corner radius of buttons, inputs and navigation items, e.g. `0.5rem`."
    )
    border_width: Length = Field(description="The width of borders, e.g. `1px`.")
    font_size: Length = Field(default="15px", description="The size of the app's text, e.g. `15px`.")
    font_size_small: Length = Field(default="13px", description="The size of secondary text, e.g. `13px`.")
    icon_size: Length = Field(default="18px", description="The size of icons, e.g. `18px`.")
    row_height: Length = Field(default="36px", description="The height of navigation and list rows, e.g. `36px`.")
    heading_weight: int = Field(
        default=600, ge=WEIGHT_MIN, le=WEIGHT_MAX, strict=True, description="The weight of headings, e.g. `600`."
    )
    heading_tracking: SignedLength = Field(
        default="0", description="The letter spacing of headings, e.g. `-0.5px` or `0`."
    )
    spacing: Length = Field(default="0.25rem", description="The base step of gaps and padding, e.g. `0.25rem`.")
    fonts: tuple[ThemeFont, ...] = Field(
        default=(),
        max_length=MAX_FONTS,
        description="Font files the theme ships (`[[fonts]]` in its file); its stacks name their families.",
    )
    logo: ThemeLogo | None = Field(
        default=None, description="The logo the theme ships (`[logo]` in its file), SVG files beside it; none if not."
    )
    variant: tuple[ThemeVariant, ...] = Field(
        default=(),
        max_length=MAX_VARIANTS,
        description="Colours that follow the time of day and the weather (`[[variant]]` in its file, ADR 0058).",
    )

    @model_validator(mode="after")
    def _readable(self) -> "Theme":
        """Refuse a theme whose text is hard to read, in either mode, as it is or in any variant (ADR 0058).

        Raises:
            ValueError: If text falls under 4.5:1 on a surface it sits on.
        """
        problems: list[str] = []
        for mode in ("light", "dark"):
            base: ThemeColours = getattr(self, mode)
            cases = [(mode, base)] + [
                (f"variant.{index}.{mode}", apply(base, variant.overrides(mode)))
                for index, variant in enumerate(self.variant)
            ]
            problems += [f"{where}: {problem}" for where, colours in cases for problem in contrast_problems(colours)]
        if problems:
            raise ValueError("text must stay readable: " + "; ".join(problems))
        return self

    def colours(self, mode: Literal["light", "dark"], signals: Signals) -> ThemeColours:
        """The colours of `mode` with the variant `signals` select applied."""
        index = variant_of(self.variant, signals)
        base: ThemeColours = getattr(self, mode)
        return base if index is None else apply(base, self.variant[index].overrides(mode))


@dataclass(frozen=True, kw_only=True, slots=True)
class ThemeLet(Let):
    """A theme a module offers (`module.theme`), named after it; consumed by `system.theme`."""

    theme: Theme
    directory: Path | None = None
    """The directory of the theme's file, which its font files are relative to; None for a theme built in Python."""

    def __post_init__(self) -> None:
        """Check the theme again, so one built with `model_construct` can't skip the grammar, and its font files.

        Raises:
            ValueError: If the theme isn't valid, is called `default`, the let isn't named after it, a font file
                is missing, leaves the theme's directory, isn't a woff2 file or is larger than 2 MB, or a logo file
                doesn't pass the checks of `logo_file`.
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
        if theme.fonts and self.directory is None:
            msg = f"Theme {theme.name!r} ships fonts, so it comes from a theme file: font files are relative to it"
            raise ValueError(msg)
        if theme.logo and self.directory is None:
            msg = f"Theme {theme.name!r} ships a logo, so it comes from a theme file: logo files are relative to it"
            raise ValueError(msg)
        for font in theme.fonts:
            font_file(self._directory(), font)
        if theme.logo:
            for file in theme.logo.files():
                logo_file(self._directory(), file)
        object.__setattr__(self, "theme", theme)

    def logos(self) -> "ThemeLogoFiles | None":
        """The theme's logo files, to read and serve; None if it ships no logo."""
        if self.theme.logo is None or self.directory is None:
            return None
        return ThemeLogoFiles(theme=self.theme.name, logo=self.theme.logo, directory=self.directory)

    def _directory(self) -> Path:
        if self.directory is None:
            msg = f"Theme {self.theme.name!r} ships no fonts"
            raise ValueError(msg)
        return self.directory

    def _font(self, index: int) -> ThemeFont:
        if not 0 <= index < len(self.theme.fonts):
            msg = f"Theme {self.theme.name!r} has no font {index}"
            raise ValueError(msg)
        return self.theme.fonts[index]

    def font(self, index: int) -> bytes:
        """The content of font `index`, checked again as it is read.

        Raises:
            ValueError: If there is no such font, or its file no longer passes the checks of `font_file`.
        """
        return read_font(self._directory(), self._font(index))

    def font_url(self, index: int) -> str:
        """Where the web head serves font `index`, with a version of its content, so a changed font is a new URL.

        Raises:
            ValueError: If there is no such font, or its file no longer passes the checks of `read_font`.
        """
        version = hashlib.sha256(self.font(index)).hexdigest()[:16]
        return f"{FONT_ROUTE}/{self.theme.name}/{index}.woff2?v={version}"


def font_file(directory: Path, font: ThemeFont) -> Path:
    """The resolved path of `font` in `directory`, after checking it is a woff2 file of at most 2 MB inside it.

    Symlinks are resolved before the check, so one pointing out of the theme's directory is refused.

    Raises:
        ValueError: If the file is missing, leads out of the directory, isn't a woff2 file or is too large.
    """
    where = f"Font file {font.file!r} of {directory}"
    try:
        root = directory.resolve(strict=True)
        path = (root / font.file).resolve(strict=True)
    except OSError as exc:
        msg = f"{where} doesn't exist; its path is relative to the theme file's directory"
        raise ValueError(msg) from exc
    if not path.is_relative_to(root):
        msg = f"{where} leads out of the theme's directory; keep font files beside the theme file"
        raise ValueError(msg)
    if not path.is_file():
        msg = f"{where} isn't a file"
        raise ValueError(msg)
    if path.stat().st_size > MAX_FONT_BYTES:
        msg = f"{where} is larger than {MAX_FONT_BYTES // 1024 // 1024} MB; subset the font"
        raise ValueError(msg)
    with path.open("rb") as handle:
        if handle.read(len(WOFF2_SIGNATURE)) != WOFF2_SIGNATURE:
            msg = f"{where} isn't a woff2 file; convert it, e.g. with woff2_compress"
            raise ValueError(msg)
    return path


def read_font(directory: Path, font: ThemeFont) -> bytes:
    """The content of `font`, checked by `font_file` and again on the bytes read, so a file swapped meanwhile is caught.

    Raises:
        ValueError: If the file doesn't pass the checks of `font_file`, can't be read, or changed meanwhile.
    """
    path = font_file(directory, font)
    content = _read_regular(path, MAX_FONT_BYTES, f"Font file {font.file!r} of {directory}")
    if len(content) > MAX_FONT_BYTES or not content.startswith(WOFF2_SIGNATURE):
        msg = f"Font file {font.file!r} of {directory} changed into something that isn't a woff2 file of up to 2 MB"
        raise ValueError(msg)
    return content


def _read_regular(path: Path, limit: int, where: str) -> bytes:
    """Up to `limit + 1` bytes of `path`, through one descriptor that follows no symlink and must be a regular file.

    Raises:
        ValueError: If `path` is a symlink, a FIFO or anything else that isn't a regular file, or can't be read.
    """
    try:
        # One descriptor for the check and the read: no symlink swapped in, no FIFO to block on, a regular file.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                msg = f"{where} isn't a file"
                raise ValueError(msg)
            return handle.read(limit + 1)
    except OSError as exc:
        msg = f"{where} can't be read: {exc}"
        raise ValueError(msg) from exc


def _local(name: str) -> str:
    """An XML name without its `{namespace}`, lowercase."""
    return name.rpartition("}")[2].lower()


def _svg_text_problem(text: str) -> str | None:
    """Why the text of an SVG file could run or load something, or None; checked before it is parsed."""
    lowered = text.lower()
    for marker, problem in (
        ("<!doctype", "declares a DOCTYPE; entities aren't allowed"),
        ("<!entity", "declares an entity; entities aren't allowed"),
        ("<script", "holds a <script>"),
        ("<foreignobject", "holds a <foreignObject>"),
        ("xml-stylesheet", "links a stylesheet"),
        ("@import", "imports a stylesheet"),
        ("image-set(", "loads an image (image-set)"),
    ):
        if marker in lowered:
            return problem
    for match in re.finditer(r"url\(\s*['\"]?\s*(.?)", lowered):
        if match.group(1) != "#":
            return "refers to something outside itself (url(...)); only url(#id) is allowed"
    return None


def _svg_element_problem(element: ET.Element) -> str | None:
    """Why one element of an SVG file could run or load something, or None."""
    tag = _local(element.tag)
    if tag in {"script", "foreignobject"}:
        return f"holds a <{tag}>"
    for name, value in element.attrib.items():
        attribute = _local(name)
        if attribute.startswith("on"):
            return f"has an event handler ({attribute}=)"
        if attribute in _REFERENCES and not value.strip().startswith("#"):
            return f"refers to something outside itself ({attribute}={value[:40]!r}); only #id is allowed"
        if tag in {"set", "animate"} and attribute == "attributename":
            target = value.strip().lower().rpartition(":")[2]
            if target in _REFERENCES or target.startswith("on"):
                return f"animates {target}, which could point outside the file"
    return _svg_css_problem(tag, element)


def _svg_css_problem(tag: str, element: ET.Element) -> str | None:
    """Why the decoded text and style of one element could load something, or None."""
    styles = [value for name, value in element.attrib.items() if _local(name) == "style"]
    if tag == "style":
        styles.append(element.text or "")
    if any("\\" in style for style in styles):
        return f"escapes characters in a {'<style>' if tag == 'style' else 'style'} (\\), which could hide a url(...)"
    # Character references are decoded by now: `&#117;rl(` is `url(` here, so the text checks run again.
    decoded = [element.text, element.tail, *element.attrib.values()]
    return next((problem for text in decoded if text and (problem := _svg_text_problem(text))), None)


def svg_problem(content: bytes) -> str | None:
    """Why `content` isn't a safe SVG logo, or None.

    A logo is UTF-8 XML of at most 200 KB whose root is `<svg>`, without a DOCTYPE or entities, a `<script>`, a
    `<foreignObject>`, an event-handler attribute (`on...=`), or a reference (`href`, `xlink:href`, `src`, `url(...)`)
    to anything but an element of the same file (`#id`): no external file, no `data:` URI.
    """
    if len(content) > MAX_LOGO_BYTES:
        return f"is larger than {MAX_LOGO_BYTES // 1024} KB; simplify it, e.g. with svgo"
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return "isn't UTF-8 text"
    return _svg_text_problem(text) or _svg_tree_problem(content)


def _svg_tree_problem(content: bytes) -> str | None:
    """Why the parsed elements of an SVG file aren't a safe logo, or None; its text passed `_svg_text_problem`."""
    try:
        root = ET.fromstring(content)  # noqa: S314 - DOCTYPE and entities are refused before: nothing expands
    except ET.ParseError as exc:
        return f"isn't well-formed XML: {exc}"
    if root.tag not in {"svg", f"{_SVG}svg"}:
        return "isn't an SVG image: its root element isn't <svg>"
    return next((problem for element in root.iter() if (problem := _svg_element_problem(element))), None)


def logo_file(directory: Path, file: str) -> Path:
    """The resolved path of logo `file` in `directory`, after checking it is a safe SVG file of at most 200 KB in it.

    Symlinks are resolved before the check, so one pointing out of the theme's directory is refused.

    Raises:
        ValueError: If the file is missing, leads out of the directory, isn't a file, or isn't a safe SVG logo.
    """
    where = f"Logo file {file!r} of {directory}"
    if not re.fullmatch(LOGO_FILE, file):
        msg = f"{where} isn't {_LOGO_PATH_RULE}logo/light.svg"
        raise ValueError(msg)
    try:
        root = directory.resolve(strict=True)
        path = (root / file).resolve(strict=True)
    except OSError as exc:
        msg = f"{where} doesn't exist; its path is relative to the theme file's directory"
        raise ValueError(msg) from exc
    if not path.is_relative_to(root):
        msg = f"{where} leads out of the theme's directory; keep logo files beside the theme file"
        raise ValueError(msg)
    if not path.is_file():
        msg = f"{where} isn't a file"
        raise ValueError(msg)
    if path.stat().st_size > MAX_LOGO_BYTES:
        msg = f"{where} is larger than {MAX_LOGO_BYTES // 1024} KB; simplify it, e.g. with svgo"
        raise ValueError(msg)
    if problem := svg_problem(path.read_bytes()):
        msg = f"{where} {problem}"
        raise ValueError(msg)
    return path


def read_logo(directory: Path, file: str) -> bytes:
    """The content of logo `file`, checked by `logo_file` and again on the bytes read, so a file swapped is caught.

    Raises:
        ValueError: If the file doesn't pass the checks of `logo_file`, can't be read, or changed meanwhile.
    """
    path = logo_file(directory, file)
    where = f"Logo file {file!r} of {directory}"
    content = _read_regular(path, MAX_LOGO_BYTES, where)
    if problem := svg_problem(content):
        msg = f"{where} changed and now {problem}"
        raise ValueError(msg)
    return content


@dataclass(frozen=True, kw_only=True, slots=True)
class ThemeLogoFiles:
    """The logo files of one theme where they are on disk: what the web head serves at `LOGO_ROUTE`."""

    theme: str
    """The theme's name, as in the URL."""
    logo: ThemeLogo
    directory: Path
    """The directory the logo's files are relative to."""

    def content(self, name: str) -> bytes:
        """The content of logo `name` (`light`, `dark` falling back to `light`, or `mark`), checked as it is read.

        Raises:
            ValueError: If the theme has no such logo, or its file doesn't pass the checks of `read_logo`.
        """
        file = self.logo.file(name)
        if file is None:
            msg = f"Theme {self.theme!r} has no {name!r} logo"
            raise ValueError(msg)
        return read_logo(self.directory, file)

    def url(self, name: str) -> str:
        """Where the web head serves logo `name`, with a version of its content, so a changed logo is a new URL.

        Raises:
            ValueError: If the theme has no such logo, or its file doesn't pass the checks of `read_logo`.
        """
        version = hashlib.sha256(self.content(name)).hexdigest()[:16]
        return f"{LOGO_ROUTE}/{self.theme}/{name}.svg?v={version}"


class ThemeFonts:
    """The font files and logos of the available themes, by theme name; provided by `system.theme` (ADR 0044).

    The web head serves them at `FONT_ROUTE` and `LOGO_ROUTE`; the kernel declares the interface, so the head needn't
    know the module.
    """

    def font(self, theme: str, index: int) -> bytes | None:
        """The content of font `index` of the available theme `theme`, or None if there is no such font."""
        raise NotImplementedError

    def logo(self, theme: str, name: str) -> bytes | None:
        """The content of logo `name` (`light`, `dark`, `mark`) of the available theme `theme`, or None if none."""
        raise NotImplementedError


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
    return tuple(theme for theme, _ in sourced_themes(source))


def sourced_themes(source: Path | Theme) -> tuple[tuple[Theme, Path | None], ...]:
    """Each theme of `source` with the directory of its file, which its font files are relative to.

    Raises:
        ValueError: If a file isn't a valid theme, or a directory holds none.
        OSError: If a file can't be read.
    """
    if isinstance(source, Theme):
        return ((source, None),)
    files = sorted(source.glob(f"*{THEME_SUFFIX}")) if source.is_dir() else [source]
    if not files:
        msg = f"{source} holds no theme files (*{THEME_SUFFIX})"
        raise ValueError(msg)
    themes: list[tuple[Theme, Path | None]] = []
    for file in files:
        if file.suffix != THEME_SUFFIX:
            msg = f"Theme file {file} must end in {THEME_SUFFIX}"
            raise ValueError(msg)
        try:
            themes.append((Theme.model_validate(tomllib.loads(file.read_text(encoding="utf-8"))), file.parent))
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
    # The web app's own palette (web/src/index.css): neutral greys after a desktop chat app, a clay primary.
    light=ThemeColours(
        background="#ffffff",
        foreground="#1a1a1a",
        muted="#f2f2f2",
        muted_foreground="#6b6b6b",
        card="#ffffff",
        border="#e4e4e4",
        primary="#b4532f",
        primary_foreground="#ffffff",
        accent="#ebebeb",
        ok="#1f7444",
        warn="#8a5c00",
        bad="#b4372d",
        ring="#b4532f",
        sidebar="#f3f3f3",
        input="#ffffff",
        popover="#ffffff",
        accent_foreground="#1a1a1a",
        icon="#5c5c5c",
    ),
    dark=ThemeColours(
        background="#181818",
        foreground="#ececec",
        muted="#272727",
        muted_foreground="#9b9b9b",
        card="#181818",
        border="#2c2c2c",
        primary="#d97757",
        primary_foreground="#ffffff",
        accent="#444444",
        ok="#7cc39a",
        warn="#e0b45f",
        bad="#e5806f",
        ring="#d97757",
        sidebar="#272727",
        input="#303030",
        popover="#2c2c2c",
        accent_foreground="#ececec",
        icon="#b3b3b3",
    ),
    font_sans='ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif',
    font_mono='ui-monospace, "SF Mono", Menlo, monospace',
    radius="0.75rem",
    radius_control="0.5rem",
    border_width="1px",
    font_size="15px",
    font_size_small="13px",
    icon_size="18px",
    row_height="36px",
    heading_weight=600,
    heading_tracking="0",
    spacing="0.25rem",
)
"""The built-in theme: always available, the one in effect until the person picks another.

It names no logo, so a plugin can copy it (`DEFAULT_THEME.model_dump() | {...}`) for a theme of its own; its logo is
`DEFAULT_LOGO`, shipped inside the package.
"""

DEFAULT_LOGO = ThemeLogoFiles(
    theme=DEFAULT,
    logo=ThemeLogo(
        light="logo/wordmark-light.svg",
        dark="logo/wordmark-dark.svg",
        mark="logo/mark.svg",
        alt="Heinzelmännchen",
        height="24px",
    ),
    directory=Path(__file__).parent / "assets" / "theme",
)
"""The built-in theme's logo: the Heinzelmännchen mark and wordmark, package data of the kernel."""
