"""Themes: how the web app looks, as data a plugin ships (ADR 0044).

A theme is a name, a title, the colours of the web app's tokens for light and dark, two font stacks, its type scale,
density and corner radii, a border width, and optionally font files of its own. Every value ends up as a CSS custom
property, so each is checked against a grammar that can't leave its declaration: hex colours, a CSS length, an integer
weight, and font stacks of family names from a small character set. A font file is a `.woff2` inside the theme's own
directory, checked at import and again when the web head serves it from its own origin: the web head's CSP
(`font-src 'self'`) loads no remote font.
"""

import hashlib
import os
import re
import stat
import tomllib
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


@dataclass(frozen=True, kw_only=True, slots=True)
class ThemeLet(Let):
    """A theme a module offers (`module.theme`), named after it; consumed by `system.theme`."""

    theme: Theme
    directory: Path | None = None
    """The directory of the theme's file, which its font files are relative to; None for a theme built in Python."""

    def __post_init__(self) -> None:
        """Check the theme again, so one built with `model_construct` can't skip the grammar, and its font files.

        Raises:
            ValueError: If the theme isn't valid, is called `default`, the let isn't named after it, or a font file
                is missing, leaves the theme's directory, isn't a woff2 file or is larger than 2 MB.
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
        for font in theme.fonts:
            font_file(self._directory(), font)
        object.__setattr__(self, "theme", theme)

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
    try:
        # One descriptor for the check and the read: no symlink swapped in, no FIFO to block on, a regular file.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                msg = f"Font file {font.file!r} of {directory} isn't a file"
                raise ValueError(msg)
            content = handle.read(MAX_FONT_BYTES + 1)
    except OSError as exc:
        msg = f"Font file {font.file!r} of {directory} can't be read: {exc}"
        raise ValueError(msg) from exc
    if len(content) > MAX_FONT_BYTES or not content.startswith(WOFF2_SIGNATURE):
        msg = f"Font file {font.file!r} of {directory} changed into something that isn't a woff2 file of up to 2 MB"
        raise ValueError(msg)
    return content


class ThemeFonts:
    """The font files of the available themes, by theme name and index; provided by `system.theme` (ADR 0044).

    The web head serves them at `FONT_ROUTE`; the kernel declares the interface, so the head needn't know the module.
    """

    def font(self, theme: str, index: int) -> bytes | None:
        """The content of font `index` of the available theme `theme`, or None if there is no such font."""
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
    # The web app's own palette (web/src/index.css); accent_foreground and icon follow foreground and muted_foreground.
    light=ThemeColours(
        background="#ffffff",
        foreground="#1a1a1a",
        muted="#f2f2f2",
        muted_foreground="#6b6b6b",
        card="#ffffff",
        border="#e4e4e4",
        primary="#c96442",
        primary_foreground="#ffffff",
        accent="#ebebeb",
        ok="#2f8a57",
        warn="#a87412",
        bad="#c0453a",
        ring="#c96442",
        sidebar="#f3f3f3",
        input="#ffffff",
        popover="#ffffff",
        accent_foreground="#1a1a1a",
        icon="#6b6b6b",
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
        icon="#9b9b9b",
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
"""The built-in theme: always available, the one in effect until the person picks another."""
