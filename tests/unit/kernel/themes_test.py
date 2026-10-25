"""Themes as data: every value is checked before it can become a CSS custom property (ADR 0044)."""

import os
import re
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from heinzel import Module, Theme, ThemeColours, ThemeFont, ThemeFonts, ThemeLet, ThemeLogo
from heinzel.kernel import (
    DEFAULT_LOGO,
    DEFAULT_THEME,
    FONT_ROUTE,
    LOGO_CSP,
    LOGO_ROUTE,
    ThemeLogoFiles,
    font_stack_problem,
)
from heinzel.kernel import _themes as themes
from heinzel.kernel._themes import (
    LOGO_NAMES,
    MAX_FONT_BYTES,
    MAX_LOGO_BYTES,
    font_file,
    logo_file,
    read_font,
    read_logo,
    svg_problem,
    themes_in,
)

pytestmark = pytest.mark.unit

COLOURS = DEFAULT_THEME.light.model_dump(exclude_none=True)


def theme(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = DEFAULT_THEME.model_dump() | {"name": "acme", "title": "Acme"}
    return data | overrides


TOML = """name = "acme"
title = "Acme"
font_sans = "Avenir Next, system-ui, sans-serif"
font_mono = 'ui-monospace, "SF Mono", monospace'
radius = "0.75rem"
border_width = "1px"

[light]
{colours}

[dark]
{colours}
""".format(colours="\n".join(f'{key} = "{value}"' for key, value in COLOURS.items()))


def test_the_default_theme_is_valid_and_complete():
    assert Theme.model_validate(DEFAULT_THEME.model_dump()) == DEFAULT_THEME
    assert DEFAULT_THEME.name == "default"
    assert set(ThemeColours.model_fields) == {
        "background",
        "foreground",
        "muted",
        "muted_foreground",
        "card",
        "border",
        "primary",
        "primary_foreground",
        "accent",
        "ok",
        "warn",
        "bad",
        "ring",
        "sidebar",
        "input",
        "popover",
        "accent_foreground",
        "icon",
        "main_tint",
        "backdrop",
    }


def test_colours_are_hex_and_kept_lowercase():
    assert ThemeColours.model_validate(COLOURS | {"primary": "#0F766E", "ok": "#abc"}).primary == "#0f766e"
    for bad in ("0f766e", "#0f766", "#0f766eff", "red", "rgb(0,0,0)", "#fff;}", "#fff\n", "var(--x)", ""):
        with pytest.raises(ValidationError):
            ThemeColours.model_validate(COLOURS | {"primary": bad})


def test_a_theme_has_every_token_and_nothing_else():
    with pytest.raises(ValidationError, match="popover"):
        ThemeColours.model_validate({k: v for k, v in COLOURS.items() if k != "popover"})
    with pytest.raises(ValidationError, match="Extra inputs"):
        ThemeColours.model_validate(COLOURS | {"shadow": "#000"})
    with pytest.raises(ValidationError, match="Extra inputs"):
        Theme.model_validate(theme(css="body { display: none }"))


@pytest.mark.parametrize("length", ["0", "0px", "1px", ".5px", "000px", "0.5rem", ".75em", "12px", "1.125rem"])
def test_lengths_are_plain_css_lengths(length):
    assert Theme.model_validate(theme(radius=length, border_width=length)).radius == length


@pytest.mark.parametrize(
    "length",
    [
        "",
        "1",
        "0.px",
        "1.12345rem",
        "-1px",
        "1 px",
        "1px;",
        "1px}",
        "calc(1px + 1rem)",
        "var(--x)",
        "1%",
        "1vh",
        "1px\n",
        "1e3px",
        "9999px",
    ],
)
def test_lengths_that_could_break_out_or_mean_anything_else_are_refused(length):
    with pytest.raises(ValidationError):
        Theme.model_validate(theme(radius=length))
    with pytest.raises(ValidationError):
        Theme.model_validate(theme(border_width=length))


@pytest.mark.parametrize(
    "stack",
    [
        "system-ui",
        "ui-sans-serif, system-ui, -apple-system, sans-serif",
        'Avenir Next, "Segoe UI", sans-serif',
        '"SF Mono", Menlo, monospace',
        '"Noto Sans 3.0_x-y"',
    ],
)
def test_font_stacks_of_family_names_pass(stack):
    assert font_stack_problem(stack) is None


@pytest.mark.parametrize(
    "stack",
    [
        "",
        " , ",
        "Arial;",
        "Arial; } body { display: none",
        "Arial}",
        "Arial{",
        'url("https://fonts.example.com/x.woff2")',
        "url(x)",
        "var(--font)",
        "'Arial'",
        '"Ar"ial"',
        '"Arial',
        'Arial"',
        '"a\\"',
        "Arial\\",
        "Arial\nsans-serif",
        "system-ui\n, sans-serif",
        "Arial\x1c,sans-serif",
        "Arial\u00a0Black",
        "\uff21rial",
        "Foo 2",
        "inherit",
        "sans-serif, initial",
        "Unset",
        "Arial/*x*/",
        "Arial !important",
        "Arial, , sans-serif",
        "Ar ial;x:y",
        "<script>",
        "a" * 301,
    ],
)
def test_font_stacks_that_could_break_out_of_their_declaration_are_refused(stack):
    assert font_stack_problem(stack) is not None
    with pytest.raises(ValidationError, match="font"):
        Theme.model_validate(theme(font_sans=stack))
    with pytest.raises(ValidationError, match="font"):
        Theme.model_validate(theme(font_mono=stack))


def test_font_stacks_are_normalised_to_one_space_after_each_comma():
    assert Theme.model_validate(theme(font_sans="  Avenir Next ,system-ui,sans-serif ")).font_sans == (
        "Avenir Next, system-ui, sans-serif"
    )


def test_names_are_slugs_and_titles_one_printable_line():
    for name in ("Acme", "acme_dark", "acme--dark", "-acme", "acme-", "", "a" * 65):
        with pytest.raises(ValidationError):
            Theme.model_validate(theme(name=name))
    for title in ("", "Acme\nDark", "Acme\x1b[31m", "a" * 65):
        with pytest.raises(ValidationError):
            Theme.model_validate(theme(title=title))
    assert Theme.model_validate(theme(title='Acme <Dark> & "Co"')).title == 'Acme <Dark> & "Co"'  # React escapes


def test_themes_are_read_from_a_toml_file_or_a_directory_of_them(tmp_path):
    (tmp_path / "acme.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "acme-night.toml").write_text(TOML.replace('"acme"', '"acme-night"'), encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a theme", encoding="utf-8")
    (only,) = themes_in(tmp_path / "acme.toml")
    assert (only.name, only.radius, only.font_mono) == ("acme", "0.75rem", 'ui-monospace, "SF Mono", monospace')
    assert [t.name for t in themes_in(tmp_path)] == ["acme-night", "acme"]
    assert themes_in(DEFAULT_THEME) == (DEFAULT_THEME,)


def test_a_bad_theme_file_says_which_file_and_which_field(tmp_path):
    (tmp_path / "broken.toml").write_text("name = ", encoding="utf-8")
    with pytest.raises(ValueError, match=r"broken.toml isn't valid TOML"):
        themes_in(tmp_path / "broken.toml")
    (tmp_path / "evil.toml").write_text(TOML.replace("1px", "1px; } body { display: none"), encoding="utf-8")
    with pytest.raises(ValueError, match=r"evil.toml isn't a valid theme: border_width"):
        themes_in(tmp_path / "evil.toml")
    (tmp_path / "acme.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match=r"must end in \.toml"):
        themes_in(tmp_path / "acme.json")
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="holds no theme files"):
        themes_in(tmp_path / "empty")
    with pytest.raises(OSError, match="missing"):
        themes_in(tmp_path / "missing.toml")


def test_a_module_offers_themes_as_lets_named_after_them(tmp_path):
    (tmp_path / "acme.toml").write_text(TOML, encoding="utf-8")
    module = Module("acme", "Acme.")
    (let,) = module.theme(tmp_path / "acme.toml")
    module.theme(Theme.model_validate(theme(name="acme-night")))
    assert let == ThemeLet(name="acme", theme=let.theme, directory=tmp_path)
    assert [let.name for let in module.lets if isinstance(let, ThemeLet)] == ["acme", "acme-night"]
    with pytest.raises(ValueError, match="already has a ThemeLet named 'acme'"):
        module.theme(tmp_path / "acme.toml")


def test_a_theme_built_without_validation_is_checked_again_when_offered():
    evil = Theme.model_construct(**(dict(DEFAULT_THEME) | {"name": "acme", "radius": "1px;}body{display:none"}))
    with pytest.raises(ValueError, match=r"Theme 'acme' isn't valid: radius: a CSS length"):
        Module("acme", "Acme.").theme(evil)
    with pytest.raises(ValueError, match="isn't valid"):
        ThemeLet(name="acme", theme=evil)
    with pytest.raises(ValueError, match="named after its theme"):
        ThemeLet(name="other", theme=Theme.model_validate(theme()))
    with pytest.raises(ValueError, match="that is the built-in one"):
        ThemeLet(name="default", theme=DEFAULT_THEME)


def test_a_nested_colour_set_built_without_validation_is_checked_again():
    evil = ThemeColours.model_construct(**(COLOURS | {"primary": "red;}"}))
    with pytest.raises(ValidationError):
        Theme.model_validate(theme() | {"light": evil})


def test_problems_in_a_theme_file_read_as_rules(tmp_path):
    (tmp_path / "acme.toml").write_text(TOML.replace("#ffffff", "white").replace("0.75rem", "big"), encoding="utf-8")
    with pytest.raises(ValueError, match=r"light\.card: a hex colour") as raised:
        themes_in(tmp_path / "acme.toml")
    assert "radius: a CSS length" in str(raised.value)
    assert "Value error" not in str(raised.value)


def test_no_module_may_offer_a_theme_called_default():
    with pytest.raises(ValueError, match="that is the built-in one"):
        Module("acme", "Acme.").theme(DEFAULT_THEME)


# Type scale, density, icon colours and font files (ADR 0044, amended 2026-10-24).

WOFF2 = b"wOF2\x00\x01\x00\x00made-up font data"
LENGTH_FIELDS = ("radius_control", "font_size", "font_size_small", "icon_size", "row_height", "spacing")
INJECTIONS = ("1px;", "1px}", "1px; } body { display: none", "var(--x)", "calc(1px)", "url(x)", "1px\n", "1px/**/")


def test_the_default_theme_matches_the_app():
    assert (DEFAULT_THEME.radius, DEFAULT_THEME.radius_control) == ("0.75rem", "0.5rem")
    assert (DEFAULT_THEME.font_size, DEFAULT_THEME.font_size_small) == ("15px", "13px")
    assert (DEFAULT_THEME.icon_size, DEFAULT_THEME.row_height, DEFAULT_THEME.spacing) == ("18px", "36px", "0.25rem")
    assert (DEFAULT_THEME.heading_weight, DEFAULT_THEME.heading_tracking, DEFAULT_THEME.fonts) == (600, "0", ())
    assert (DEFAULT_THEME.dark.background, DEFAULT_THEME.dark.primary) == ("#181818", "#d97757")
    assert (DEFAULT_THEME.light.background, DEFAULT_THEME.light.primary) == ("#ffffff", "#b4532f")


def test_a_theme_without_the_new_fields_gets_their_defaults(tmp_path):
    older = TOML.replace('radius = "0.75rem"\n', "")
    for token in ("accent_foreground", "icon"):
        older = "\n".join(line for line in older.splitlines() if not line.startswith(f"{token} ="))
    (tmp_path / "acme.toml").write_text(older, encoding="utf-8")
    (read,) = themes_in(tmp_path / "acme.toml")
    assert (read.radius, read.radius_control, read.font_size, read.font_size_small) == (
        "0.75rem",
        "0.5rem",
        "15px",
        "13px",
    )
    assert (read.icon_size, read.row_height, read.spacing, read.heading_weight) == ("18px", "36px", "0.25rem", 600)
    assert (read.heading_tracking, read.fonts) == ("0", ())
    assert read.light.accent_foreground == read.light.foreground
    assert read.dark.icon == read.dark.muted_foreground


def test_the_new_colours_are_hex_like_the_others():
    colours = ThemeColours.model_validate(COLOURS | {"accent_foreground": "#0F766E", "icon": "#abc"})
    assert (colours.accent_foreground, colours.icon) == ("#0f766e", "#abc")
    for token in ("accent_foreground", "icon"):
        for bad in ("red", "#fff;}", "var(--x)", "#fff\n", "url(x)", ""):
            with pytest.raises(ValidationError, match=token):
                ThemeColours.model_validate(COLOURS | {token: bad})


@pytest.mark.parametrize("field", LENGTH_FIELDS)
def test_the_new_lengths_take_the_length_grammar(field):
    assert getattr(Theme.model_validate(theme(**{field: "1.125rem"})), field) == "1.125rem"
    for bad in (*INJECTIONS, "-1px", "1%", "1vh", "12", "9999px"):
        with pytest.raises(ValidationError, match=field):
            Theme.model_validate(theme(**{field: bad}))


def test_heading_tracking_may_be_negative_and_nothing_else():
    for tracking in ("0", "-0.5px", "0.01em", "-.02rem"):
        assert Theme.model_validate(theme(heading_tracking=tracking)).heading_tracking == tracking
    for bad in (*INJECTIONS, "--1px", "- 1px", "-1", "-1%", "+1px"):
        with pytest.raises(ValidationError, match="heading_tracking"):
            Theme.model_validate(theme(heading_tracking=bad))


def test_heading_weight_is_an_integer_from_100_to_900():
    assert Theme.model_validate(theme(heading_weight=450)).heading_weight == 450
    for bad in (99, 901, "600", "600;}", 600.5, True, None):
        with pytest.raises(ValidationError, match="heading_weight"):
            Theme.model_validate(theme(heading_weight=bad))


def test_a_problem_with_a_new_field_reads_as_its_rule(tmp_path):
    text = TOML.replace('border_width = "1px"', 'border_width = "1px"\nrow_height = "tall"\nheading_tracking = "x"')
    (tmp_path / "acme.toml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=r"row_height: a CSS length.*36px") as raised:
        themes_in(tmp_path / "acme.toml")
    assert "heading_tracking: a CSS length that may be negative" in str(raised.value)


def font(**overrides: object) -> dict[str, object]:
    return {"family": "Acme Sans", "file": "fonts/acme-sans.woff2", "weight": "400", "style": "normal"} | overrides


def test_a_font_entry_takes_a_family_a_relative_woff2_path_a_weight_and_a_style():
    assert ThemeFont.model_validate(font(weight="100 900", style="italic")).weight == "100 900"
    assert ThemeFont.model_validate({"family": "Acme Sans 2.0", "file": "acme.woff2"}).style == "normal"
    for family in ('Acme"', "Acme;", "Acme}", "url(x)", "var(--x)", "Acme\nSans", "'Acme'", " Acme", "Acme  Sans", ""):
        with pytest.raises(ValidationError, match="family"):
            ThemeFont.model_validate(font(family=family))
    with pytest.raises(ValidationError, match="family"):
        ThemeFont.model_validate(font(family="a" * 65))


@pytest.mark.parametrize(
    "file",
    [
        "../acme.woff2",
        "fonts/../../acme.woff2",
        "fonts/./acme.woff2",
        "/etc/acme.woff2",
        "C:/acme.woff2",
        "fonts\\acme.woff2",
        ".acme.woff2",
        "fonts//acme.woff2",
        "acme.ttf",
        "acme.woff",
        "acme.woff2;",
        "acme.woff2\n",
        "acme.woff2/",
        "url(acme).woff2",
        "acme%2e.woff2",
        "a c.woff2",
        "~/acme.woff2",
        "",
        "a/b/c/d/e/f/g/h/acme.woff2",
        "a" * 200 + ".woff2",
    ],
)
def test_a_font_path_that_could_leave_the_theme_directory_or_be_anything_else_is_refused(file):
    with pytest.raises(ValidationError, match="file"):
        ThemeFont.model_validate(font(file=file))


@pytest.mark.parametrize("weight", ["50", "1000", "900 100", "400;", "bold", "400  700", "400 500 600", "", "4000"])
def test_a_font_weight_is_one_or_two_weights_from_100_to_900(weight):
    with pytest.raises(ValidationError, match="weight"):
        ThemeFont.model_validate(font(weight=weight))


def test_a_font_style_is_normal_or_italic():
    with pytest.raises(ValidationError, match="style"):
        ThemeFont.model_validate(font(style="oblique 10deg"))
    with pytest.raises(ValidationError, match="Extra inputs"):
        ThemeFont.model_validate(font(src="url(https://fonts.example.com/x.woff2)"))


def a_theme_dir(tmp_path: Path, content: bytes = WOFF2, file: str = "fonts/acme-sans.woff2") -> Path:
    directory = tmp_path / "themes"
    (directory / "fonts").mkdir(parents=True, exist_ok=True)
    (directory / file).write_bytes(content)
    fonts = f'[[fonts]]\nfamily = "Acme Sans"\nfile = "{file}"\nweight = "100 900"\n'
    (directory / "acme.toml").write_text(TOML.replace("[light]", fonts + "\n[light]"), encoding="utf-8")
    return directory


def test_a_theme_file_ships_fonts_beside_it_and_the_let_serves_them(tmp_path):
    directory = a_theme_dir(tmp_path)
    (let,) = Module("acme", "Acme.").theme(directory)
    assert let.directory == directory
    assert let.theme.fonts == (ThemeFont(family="Acme Sans", file="fonts/acme-sans.woff2", weight="100 900"),)
    assert let.font(0) == WOFF2
    assert let.font_url(0).startswith(f"{FONT_ROUTE}/acme/0.woff2?v=")
    with pytest.raises(ValueError, match="has no font 1"):
        let.font(1)
    with pytest.raises(ValueError, match="has no font -1"):
        let.font_url(-1)


def test_a_font_url_changes_when_the_file_does(tmp_path):
    directory = a_theme_dir(tmp_path)
    (let,) = Module("acme", "Acme.").theme(directory)
    before = let.font_url(0)
    (directory / "fonts" / "acme-sans.woff2").write_bytes(WOFF2 + b"more")
    assert let.font_url(0) != before


def test_a_missing_font_file_is_refused_at_import(tmp_path):
    directory = a_theme_dir(tmp_path)
    (directory / "fonts" / "acme-sans.woff2").unlink()
    with pytest.raises(ValueError, match=r"'fonts/acme-sans.woff2' of .* doesn't exist"):
        Module("acme", "Acme.").theme(directory)


def test_a_font_that_is_no_woff2_or_too_large_is_refused_at_import(tmp_path):
    with pytest.raises(ValueError, match="isn't a woff2 file"):
        Module("acme", "Acme.").theme(a_theme_dir(tmp_path / "ttf", b"\x00\x01\x00\x00true type"))
    with pytest.raises(ValueError, match="larger than 2 MB"):
        Module("acme", "Acme.").theme(a_theme_dir(tmp_path / "big", WOFF2 + b"\x00" * MAX_FONT_BYTES))
    assert Module("acme", "Acme.").theme(a_theme_dir(tmp_path / "edge", WOFF2.ljust(MAX_FONT_BYTES, b"\x00")))


def test_a_font_path_that_is_a_directory_is_refused(tmp_path):
    directory = a_theme_dir(tmp_path)
    (directory / "fonts" / "acme-sans.woff2").unlink()
    (directory / "fonts" / "acme-sans.woff2").mkdir()
    with pytest.raises(ValueError, match="isn't a file"):
        Module("acme", "Acme.").theme(directory)


def test_a_symlink_out_of_the_theme_directory_is_refused(tmp_path):
    outside = tmp_path / "secret.woff2"
    outside.write_bytes(WOFF2)
    directory = a_theme_dir(tmp_path)
    link = directory / "fonts" / "acme-sans.woff2"
    link.unlink()
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="leads out of the theme's directory"):
        Module("acme", "Acme.").theme(directory)
    (directory / "fonts").rename(directory / "elsewhere")
    Path(directory / "fonts").symlink_to(tmp_path)  # a directory link out, the file name inside it ordinary
    outside.rename(tmp_path / "acme-sans.woff2")
    with pytest.raises(ValueError, match="leads out of the theme's directory"):
        Module("acme", "Acme.").theme(directory)


def test_a_symlink_inside_the_theme_directory_is_followed(tmp_path):
    directory = a_theme_dir(tmp_path)
    (directory / "fonts" / "real.woff2").write_bytes(WOFF2)
    link = directory / "fonts" / "acme-sans.woff2"
    link.unlink()
    link.symlink_to(directory / "fonts" / "real.woff2")
    (let,) = Module("acme", "Acme.").theme(directory)
    assert let.font(0) == WOFF2


def test_a_font_file_swapped_after_import_is_refused_when_read(tmp_path):
    directory = a_theme_dir(tmp_path)
    (let,) = Module("acme", "Acme.").theme(directory)
    (directory / "fonts" / "acme-sans.woff2").write_bytes(b"<html>not a font</html>")
    with pytest.raises(ValueError, match="isn't a woff2 file"):
        let.font(0)
    (directory / "fonts" / "acme-sans.woff2").unlink()
    with pytest.raises(ValueError, match="doesn't exist"):
        let.font_url(0)


def test_read_font_checks_the_bytes_it_read(tmp_path, monkeypatch):
    directory = a_theme_dir(tmp_path)
    entry = ThemeFont(family="Acme Sans", file="fonts/acme-sans.woff2")
    path = font_file(directory, entry)
    assert path == (directory / "fonts" / "acme-sans.woff2").resolve()

    def checked_then_swapped(*_: object) -> Path:  # the file changes between the check and the read
        path.write_bytes(b"<html>swapped</html>")
        return path

    monkeypatch.setattr(themes, "font_file", checked_then_swapped)
    with pytest.raises(ValueError, match="changed into something that isn't a woff2 file"):
        read_font(directory, entry)


def test_an_unreadable_font_file_is_reported(tmp_path, monkeypatch):
    directory = a_theme_dir(tmp_path)
    entry = ThemeFont(family="Acme Sans", file="fonts/acme-sans.woff2")

    def denied(*_: object) -> int:
        msg = "denied"
        raise PermissionError(msg)

    monkeypatch.setattr(themes.os, "open", denied)
    with pytest.raises(ValueError, match="can't be read: denied"):
        read_font(directory, entry)


def test_a_font_file_swapped_for_a_symlink_or_a_fifo_after_the_check_is_refused(tmp_path, monkeypatch):
    directory = a_theme_dir(tmp_path)
    entry = ThemeFont(family="Acme Sans", file="fonts/acme-sans.woff2")
    fifo = tmp_path / "fifo.woff2"
    os.mkfifo(fifo)
    link = tmp_path / "link.woff2"
    link.symlink_to(directory / "fonts" / "acme-sans.woff2")
    monkeypatch.setattr(themes, "font_file", lambda *_: fifo)
    with pytest.raises(ValueError, match="isn't a file"):
        read_font(directory, entry)  # returns at once: a FIFO isn't waited on
    monkeypatch.setattr(themes, "font_file", lambda *_: link)
    with pytest.raises(ValueError, match="can't be read"):
        read_font(directory, entry)


def test_a_theme_with_fonts_must_come_from_a_file():
    shipped = Theme.model_validate(theme(fonts=[font()]))
    with pytest.raises(ValueError, match="ships fonts, so it comes from a theme file"):
        Module("acme", "Acme.").theme(shipped)
    let = ThemeLet(name="acme", theme=Theme.model_validate(theme()))
    with pytest.raises(ValueError, match="ships no fonts"):
        let.font(0)


def test_a_font_entry_built_without_validation_is_checked_again(tmp_path):
    directory = a_theme_dir(tmp_path)
    evil = ThemeFont.model_construct(family="Acme Sans", file="../../secret.woff2", weight="400", style="normal")
    with pytest.raises(ValueError, match=r"fonts\.0\.file"):
        ThemeLet(
            name="acme",
            theme=Theme.model_construct(**(dict(Theme.model_validate(theme())) | {"fonts": (evil,)})),
            directory=directory,
        )


def test_a_theme_ships_at_most_sixteen_fonts():
    with pytest.raises(ValidationError, match="fonts"):
        Theme.model_validate(theme(fonts=[font()] * 17))


def test_theme_fonts_is_an_interface_the_theme_module_implements():
    with pytest.raises(NotImplementedError):
        ThemeFonts().font("acme", 0)


# Logos and a main tint (ADR 0044, amended 2026-10-24).

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="#0f766e"/></svg>'
LOGO_TABLE = '[logo]\nlight = "logo/light.svg"\n{more}alt = "Acme"\n'
NS = 'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'


def a_logo_dir(tmp_path: Path, content: bytes = SVG, more: str = "", files: tuple[str, ...] = ("light",)) -> Path:
    directory = tmp_path / "themes"
    (directory / "logo").mkdir(parents=True, exist_ok=True)
    for name in files:
        (directory / "logo" / f"{name}.svg").write_bytes(content)
    table = LOGO_TABLE.format(more=more)
    (directory / "acme.toml").write_text(TOML.replace("[light]", table + "\n[light]"), encoding="utf-8")
    return directory


def logo(**overrides: object) -> dict[str, object]:
    return {"light": "logo/light.svg", "alt": "Acme"} | overrides


def logos_of(directory: Path) -> ThemeLogoFiles:
    (let,) = Module("acme", "Acme.").theme(directory)
    logos = let.logos()
    assert logos is not None
    return logos


def test_a_logo_has_defaults_and_its_dark_falls_back_to_light(tmp_path):
    logos = logos_of(a_logo_dir(tmp_path))
    assert logos.logo == ThemeLogo(light="logo/light.svg", alt="Acme")
    assert (logos.logo.dark, logos.logo.mark, logos.logo.height) == (None, None, "24px")
    assert logos.content("light") == logos.content("dark") == SVG
    assert logos.url("light").startswith(f"{LOGO_ROUTE}/acme/light.svg?v=")
    assert logos.url("dark") == logos.url("light").replace("/light.svg", "/dark.svg")
    with pytest.raises(ValueError, match="has no 'mark' logo"):
        logos.url("mark")
    assert logos.logo.file("other") is None


def test_a_logo_with_every_file_serves_each(tmp_path):
    more = 'dark = "logo/dark.svg"\nmark = "logo/mark.svg"\nheight = "2rem"\n'
    directory = a_logo_dir(tmp_path, more=more, files=("light", "dark", "mark"))
    (directory / "logo" / "dark.svg").write_bytes(SVG.replace(b"#0f766e", b"#2dd4bf"))
    logos = logos_of(directory)
    assert logos.content("dark") != logos.content("light")
    assert logos.content("mark") == SVG
    assert logos.logo.height == "2rem"
    assert logos.logo.files() == ("logo/light.svg", "logo/dark.svg", "logo/mark.svg")


def test_a_logo_url_changes_when_the_file_does(tmp_path):
    directory = a_logo_dir(tmp_path)
    logos = logos_of(directory)
    before = logos.url("light")
    (directory / "logo" / "light.svg").write_bytes(SVG.replace(b"#0f766e", b"#000000"))
    assert logos.url("light") != before


def test_a_theme_without_a_logo_has_none():
    let = ThemeLet(name="acme", theme=Theme.model_validate(theme()))
    assert (let.theme.logo, let.logos()) == (None, None)


@pytest.mark.parametrize(
    "file",
    [
        "../logo.svg",
        "logo/../../x.svg",
        "/etc/logo.svg",
        ".hidden.svg",
        "logo/.x.svg",
        "logo\\x.svg",
        "logo.png",
        "logo.svg.png",
        "logo",
        "a/b/c/d/e/f/g/h/i.svg",
        "logo .svg",
    ],
)
def test_a_logo_path_must_be_plain_names_inside_the_theme_ending_in_svg(file):
    with pytest.raises(ValidationError, match="light"):
        ThemeLogo.model_validate(logo(light=file))
    with pytest.raises(ValidationError, match="mark"):
        ThemeLogo.model_validate(logo(mark=file))
    with pytest.raises(ValidationError, match="dark"):
        ThemeLogo.model_validate(logo(dark=file))


def test_a_bad_logo_path_in_a_theme_file_says_the_rule(tmp_path):
    directory = a_logo_dir(tmp_path)
    toml = (directory / "acme.toml").read_text(encoding="utf-8").replace("logo/light.svg", "../light.svg")
    (directory / "acme.toml").write_text(toml, encoding="utf-8")
    with pytest.raises(ValueError, match=r"logo\.light: a path inside the theme's directory .* ending in \.svg"):
        Module("acme", "Acme.").theme(directory)


@pytest.mark.parametrize("alt", ["", "Acme\nCorp", "Acme\x00", "A" * 65])
def test_a_logo_has_one_short_line_of_alt_text(alt):
    with pytest.raises(ValidationError, match="alt"):
        ThemeLogo.model_validate(logo(alt=alt))


def test_a_logo_needs_alt_text_and_a_light_file():
    with pytest.raises(ValidationError, match="alt"):
        ThemeLogo.model_validate({"light": "logo/light.svg"})
    with pytest.raises(ValidationError, match="light"):
        ThemeLogo.model_validate({"alt": "Acme"})


@pytest.mark.parametrize("height", INJECTIONS)
def test_a_logo_height_is_a_css_length(height):
    with pytest.raises(ValidationError, match="height"):
        ThemeLogo.model_validate(logo(height=height))


def test_a_logo_takes_no_other_keys():
    with pytest.raises(ValidationError, match="Extra inputs"):
        ThemeLogo.model_validate(logo(src="https://logo.example.com/acme.svg"))


def test_a_missing_logo_file_is_refused_at_import(tmp_path):
    directory = a_logo_dir(tmp_path)
    (directory / "logo" / "light.svg").unlink()
    with pytest.raises(ValueError, match=r"Logo file 'logo/light.svg' of .* doesn't exist"):
        Module("acme", "Acme.").theme(directory)


def test_a_logo_path_that_is_a_directory_is_refused(tmp_path):
    directory = a_logo_dir(tmp_path)
    (directory / "logo" / "light.svg").unlink()
    (directory / "logo" / "light.svg").mkdir()
    with pytest.raises(ValueError, match="isn't a file"):
        Module("acme", "Acme.").theme(directory)


def test_a_logo_symlink_out_of_the_theme_directory_is_refused(tmp_path):
    outside = tmp_path / "secret.svg"
    outside.write_bytes(SVG)
    directory = a_logo_dir(tmp_path)
    link = directory / "logo" / "light.svg"
    link.unlink()
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="leads out of the theme's directory"):
        Module("acme", "Acme.").theme(directory)


def test_a_logo_symlink_inside_the_theme_directory_is_followed(tmp_path):
    directory = a_logo_dir(tmp_path)
    (directory / "logo" / "real.svg").write_bytes(SVG)
    link = directory / "logo" / "light.svg"
    link.unlink()
    link.symlink_to(directory / "logo" / "real.svg")
    assert logos_of(directory).content("light") == SVG


def test_a_logo_larger_than_200_kb_is_refused(tmp_path):
    padding = b"<!--" + b"x" * MAX_LOGO_BYTES + b"-->"
    with pytest.raises(ValueError, match="larger than 200 KB"):
        Module("acme", "Acme.").theme(a_logo_dir(tmp_path, SVG.replace(b"<rect", padding + b"<rect")))
    assert svg_problem(SVG + b" " * (MAX_LOGO_BYTES - len(SVG))) is None
    assert (
        svg_problem(SVG + b" " * (MAX_LOGO_BYTES - len(SVG) + 1))
        == "is larger than 200 KB; simplify it, e.g. with svgo"
    )


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        (b"<html><body>not an image</body></html>", "root element isn't <svg>"),
        (b'<svg xmlns="http://www.example.com/not-svg"/>', "root element isn't <svg>"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'><rect></svg>", "isn't well-formed XML"),
        (b"\x89PNG\r\n\x1a\n\x00\x00", "isn't UTF-8 text"),
        (b"just text", "isn't well-formed XML"),
    ],
)
def test_a_logo_that_is_no_svg_is_refused(tmp_path, content, problem):
    with pytest.raises(ValueError, match=re.escape(problem)):
        Module("acme", "Acme.").theme(a_logo_dir(tmp_path, content))


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        ("<script>alert(1)</script>", "holds a <script>"),
        ("<SCRIPT>alert(1)</SCRIPT>", "holds a <script>"),
        ('<s:script xmlns:s="http://www.w3.org/2000/svg">alert(1)</s:script>', "holds a <script>"),
        ('<rect width="1" height="1" onload="alert(1)"/>', "event handler (onload=)"),
        ('<rect width="1" height="1" ONCLICK="alert(1)"/>', "event handler (onclick=)"),
        ('<foreignObject><p xmlns="http://www.w3.org/1999/xhtml">hi</p></foreignObject>', "<foreignObject>"),
        ('<s:foreignobject xmlns:s="http://www.w3.org/2000/svg"/>', "<foreignobject>"),
        ('<image href="https://tracker.example.com/pixel.png"/>', "refers to something outside itself (href="),
        ('<image xlink:href="data:image/png;base64,AAAA"/>', "refers to something outside itself (href="),
        ('<use href="other.svg#a"/>', "refers to something outside itself (href="),
        ('<a href="javascript:alert(1)"><rect width="1" height="1"/></a>', "outside itself (href="),
        ('<image src="https://tracker.example.com/pixel.png"/>', "outside itself (src="),
        ('<rect width="1" height="1" fill="url(https://tracker.example.com/x)"/>', "url(...)"),
        ('<rect width="1" height="1" style="fill: url( \'data:image/png;base64,AAAA\' )"/>', "url(...)"),
        ("<style>rect { fill: URL(x.svg#a) }</style>", "url(...)"),
        ("<style>@import 'https://css.example.com/x.css';</style>", "imports a stylesheet"),
        ("<style>rect { background: image-set('x.png' 1x) }</style>", "image-set"),
        ('<set attributeName="href" to="https://example.com"/>', "animates href"),
        ('<animate attributeName="xlink:href" values="#a;https://example.com"/>', "animates href"),
        ('<set attributeName="onclick" to="alert(1)"/>', "animates onclick"),
        ('<rect width="1" height="1" style="fill:&#117;rl(https://x.example.com)"/>', "url(...)"),
        ("<style>rect { fill: &#x75;rl(https://x.example.com) }</style>", "url(...)"),
        ('<rect width="1" height="1" style="fill: \\75rl(https://x.example.com)"/>', "escapes characters in a style"),
        ("<style>@\\69mport 'https://css.example.com/x.css';</style>", "escapes characters in a <style>"),
    ],
)
def test_a_logo_that_could_run_or_load_something_is_refused(tmp_path, body, problem):
    content = f"<svg {NS}>{body}</svg>".encode()
    with pytest.raises(ValueError, match=re.escape(problem)):
        Module("acme", "Acme.").theme(a_logo_dir(tmp_path, content))


@pytest.mark.parametrize(
    "prefix",
    [
        '<!DOCTYPE svg [<!ENTITY x "xxxxxxxx">]>',
        '<!doctype svg SYSTEM "https://dtd.example.com/svg.dtd">',
        '<!ENTITY x "x">',
        '<?xml-stylesheet href="https://css.example.com/x.css"?>',
    ],
)
def test_a_logo_with_a_doctype_an_entity_or_a_stylesheet_link_is_refused(tmp_path, prefix):
    with pytest.raises(ValueError, match=r"DOCTYPE|entity|stylesheet"):
        Module("acme", "Acme.").theme(a_logo_dir(tmp_path, prefix.encode() + SVG))


def test_a_logo_may_refer_to_its_own_elements(tmp_path):
    content = (
        f'<?xml version="1.0" encoding="UTF-8"?><svg {NS} viewBox="0 0 10 10">'
        '<defs><linearGradient id="g"><stop offset="0" stop-color="#0f766e"/></linearGradient>'
        '<path id="p" d="M0 0h10v10z"/></defs><use href="#p"/><use xlink:href=" #p"/>'
        '<rect width="10" height="10" fill="url(#g)" style="stroke: url( \'#g\' )"/>'
        '<set attributeName="fill" to="#000"/>'
        '<!-- a comment --><text font-family="system-ui, sans-serif">Acme ä</text></svg>'
    ).encode()
    assert logos_of(a_logo_dir(tmp_path, content)).content("light") == content
    assert svg_problem(SVG.replace(b'xmlns="http://www.w3.org/2000/svg" ', b"")) is None  # no namespace: still <svg>


def test_a_logo_swapped_after_import_is_refused_when_read(tmp_path):
    directory = a_logo_dir(tmp_path)
    logos = logos_of(directory)
    (directory / "logo" / "light.svg").write_bytes(f"<svg {NS}><script>alert(1)</script></svg>".encode())
    with pytest.raises(ValueError, match="holds a <script>"):
        logos.content("light")
    (directory / "logo" / "light.svg").unlink()
    with pytest.raises(ValueError, match="doesn't exist"):
        logos.url("dark")


def test_read_logo_checks_the_bytes_it_read(tmp_path, monkeypatch):
    directory = a_logo_dir(tmp_path)
    path = logo_file(directory, "logo/light.svg")
    assert path == (directory / "logo" / "light.svg").resolve()

    def checked_then_swapped(*_: object) -> Path:  # the file changes between the check and the read
        path.write_bytes(f'<svg {NS}><rect onclick="alert(1)"/></svg>'.encode())
        return path

    monkeypatch.setattr(themes, "logo_file", checked_then_swapped)
    with pytest.raises(ValueError, match="changed and now has an event handler"):
        read_logo(directory, "logo/light.svg")


def test_a_logo_swapped_for_a_symlink_or_a_fifo_after_the_check_is_refused(tmp_path, monkeypatch):
    directory = a_logo_dir(tmp_path)
    fifo = tmp_path / "fifo.svg"
    os.mkfifo(fifo)
    link = tmp_path / "link.svg"
    link.symlink_to(directory / "logo" / "light.svg")
    monkeypatch.setattr(themes, "logo_file", lambda *_: fifo)
    with pytest.raises(ValueError, match="isn't a file"):
        read_logo(directory, "logo/light.svg")  # returns at once: a FIFO isn't waited on
    monkeypatch.setattr(themes, "logo_file", lambda *_: link)
    with pytest.raises(ValueError, match="can't be read"):
        read_logo(directory, "logo/light.svg")


def test_logo_file_checks_the_path_rule_itself(tmp_path):
    with pytest.raises(ValueError, match=r"ending in \.svg"):
        logo_file(a_logo_dir(tmp_path), "../light.svg")


def test_a_theme_with_a_logo_must_come_from_a_file():
    shipped = Theme.model_validate(theme(logo=logo()))
    with pytest.raises(ValueError, match="ships a logo, so it comes from a theme file"):
        Module("acme", "Acme.").theme(shipped)


def test_a_logo_built_without_validation_is_checked_again(tmp_path):
    directory = a_logo_dir(tmp_path)
    evil = ThemeLogo.model_construct(light="../../secret.svg", dark=None, mark=None, alt="Acme", height="24px")
    with pytest.raises(ValueError, match=r"logo\.light"):
        ThemeLet(
            name="acme",
            theme=Theme.model_construct(**(dict(Theme.model_validate(theme())) | {"logo": evil})),
            directory=directory,
        )


def test_the_built_in_logo_is_package_data_that_passes_every_check():
    assert DEFAULT_THEME.logo is None  # so a plugin can copy the default theme without its files
    assert (DEFAULT_LOGO.theme, DEFAULT_LOGO.logo.alt, DEFAULT_LOGO.logo.height) == (
        "default",
        "Heinzelmännchen",
        "24px",
    )
    for name in LOGO_NAMES:
        content = DEFAULT_LOGO.content(name)
        assert svg_problem(content) is None
        assert b"currentcolor" not in content.lower()
        assert DEFAULT_LOGO.url(name).startswith(f"{LOGO_ROUTE}/default/{name}.svg?v=")
    assert "Heinzelmännchen</text>" in DEFAULT_LOGO.content("light").decode()
    assert DEFAULT_LOGO.content("light") != DEFAULT_LOGO.content("dark")


def test_theme_fonts_declares_logos_too():
    with pytest.raises(NotImplementedError):
        ThemeFonts().logo("acme", "light")
    assert LOGO_CSP == "default-src 'none'; style-src 'unsafe-inline'"


def test_main_tint_is_optional_per_mode_and_a_hex_colour():
    assert (DEFAULT_THEME.light.main_tint, DEFAULT_THEME.dark.main_tint) == (None, None)
    tinted = Theme.model_validate(theme(light=COLOURS | {"main_tint": "#F7F4EE"}, dark=COLOURS | {"main_tint": "#EEE"}))
    assert (tinted.light.main_tint, tinted.dark.main_tint) == ("#f7f4ee", "#eee")
    assert Theme.model_validate(theme(light=COLOURS | {"main_tint": None})).light.main_tint is None


@pytest.mark.parametrize("tint", ["red", "#12;", "#1234", "url(x)", "var(--x)", "#fff;}", "#ffffff\n"])
def test_main_tint_takes_the_colour_grammar(tint):
    with pytest.raises(ValidationError, match="main_tint"):
        ThemeColours.model_validate(COLOURS | {"main_tint": tint})


def variant(**overrides: object) -> dict[str, object]:
    return {"when": {"daypart": "night"}} | overrides


def test_a_variant_overrides_colours_per_mode_and_one_word_is_a_list():
    rainy = Theme.model_validate(
        theme(variant=[variant(when={"weather": "rain"}, light={"main_tint": "#EEF0F7"}, dark={"foreground": "#fff"})])
    )
    assert rainy.variant[0].when.weather == ("rain",)
    assert rainy.colours("light", {"weather": "rain"}).main_tint == "#eef0f7"
    assert rainy.colours("dark", {"weather": "rain"}).foreground == "#fff"
    assert rainy.colours("dark", {"weather": "clear"}) == rainy.dark


def test_the_variant_naming_most_signals_wins_and_the_first_on_a_tie():
    variants = (
        themes.ThemeVariant.model_validate(variant(light={"main_tint": "#eeeeee"})),
        themes.ThemeVariant.model_validate(variant(when={"weather": ["rain", "drizzle"]})),
        themes.ThemeVariant.model_validate(variant(when={"daypart": "night", "weather": "rain"})),
        themes.ThemeVariant.model_validate(variant(when={"daypart": ["night"]})),
    )
    assert themes.variant_of(variants, {"daypart": "day", "weather": None}) is None
    assert themes.variant_of(variants, {"daypart": "night", "weather": None}) == 0
    assert themes.variant_of(variants, {"daypart": "day", "weather": "drizzle"}) == 1
    assert themes.variant_of(variants, {"daypart": "night", "weather": "rain"}) == 2
    assert themes.variant_of(variants, {}) is None


@pytest.mark.parametrize(
    ("when", "match"),
    [
        ({}, "names at least one signal"),
        ({"daypart": "midnight"}, "daypart"),
        ({"weather": "sunny"}, "weather"),
        ({"mood": "happy"}, "mood"),
    ],
)
def test_a_variant_names_signals_of_the_closed_vocabulary(when, match):
    with pytest.raises(ValidationError, match=match):
        Theme.model_validate(theme(variant=[{"when": when}]))


@pytest.mark.parametrize(
    "overrides",
    [{"main_tint": "url(x)"}, {"background": "#fff;}"}, {"font_sans": "x"}, {"backdrop": {"gradient": "conic"}}],
)
def test_a_variant_takes_the_same_grammar(overrides):
    with pytest.raises(ValidationError):
        Theme.model_validate(theme(variant=[variant(light=overrides)]))


def test_a_theme_has_at_most_32_variants():
    with pytest.raises(ValidationError, match="32"):
        Theme.model_validate(theme(variant=[variant()] * 33))


def test_a_backdrop_is_a_gradient_of_two_to_four_hex_or_token_stops():
    backdrop = themes.ThemeBackdrop.model_validate({"gradient": "linear", "stops": ["primary", "#FDE68A", "main_tint"]})
    assert (backdrop.angle, backdrop.opacity, backdrop.stops) == (180, 0.6, ("primary", "#fde68a", "main_tint"))
    assert themes.ThemeBackdrop.model_validate({"gradient": "radial", "stops": ["#fff", "#000"]}).gradient == "radial"


@pytest.mark.parametrize(
    "backdrop",
    [
        {"gradient": "linear", "stops": ["#fff"]},
        {"gradient": "linear", "stops": ["#fff"] * 5},
        {"gradient": "linear", "stops": ["url(x)", "#fff"]},
        {"gradient": "linear", "stops": ["var(--x)", "#fff"]},
        {"gradient": "linear", "stops": ["backdrop", "#fff"]},
        {"gradient": "linear", "stops": ["#fff", "#000"], "opacity": 0.95},
        {"gradient": "linear", "stops": ["#fff", "#000"], "angle": 361},
        {"gradient": "linear", "stops": ["#fff", "#000"], "angle": "90deg"},
        {"gradient": "conic", "stops": ["#fff", "#000"]},
        {"gradient": "linear", "stops": ["#fff", "#000"], "image": "x.png"},
    ],
)
def test_a_backdrop_refuses_anything_outside_its_grammar(backdrop):
    with pytest.raises(ValidationError):
        themes.ThemeBackdrop.model_validate(backdrop)


def test_contrast_is_the_wcag_ratio():
    assert themes.contrast("#000", "#fff") == pytest.approx(21)
    assert themes.contrast("#ffffff", "#ffffff") == pytest.approx(1)
    assert themes.contrast("#6b6b6b", "#ffffff") == pytest.approx(5.33, abs=0.01)


@pytest.mark.parametrize(
    ("light", "where"),
    [
        ({"foreground": "#bbbbbb"}, "light: foreground on background"),
        ({"muted_foreground": "#cccccc"}, "light: muted_foreground on background"),
        ({"card": "#555555"}, "light: foreground on card"),
        ({"main_tint": "#333333"}, "light: foreground on main_tint"),
        (
            {"backdrop": {"gradient": "linear", "stops": ["#000000", "background"], "opacity": 0.9}},
            "light: foreground on backdrop stop #000000",
        ),
    ],
)
def test_a_theme_whose_text_is_hard_to_read_is_refused(light, where):
    with pytest.raises(ValidationError, match=re.escape(where)):
        Theme.model_validate(theme(light=COLOURS | light))


def test_every_variant_is_checked_for_contrast_in_both_modes():
    with pytest.raises(ValidationError, match=re.escape("variant.1.dark: foreground on background")):
        Theme.model_validate(theme(variant=[variant(), variant(dark={"background": "#dddddd"})]))
    gentle = {"backdrop": {"gradient": "linear", "stops": ["#e3e9ec", "background"], "opacity": 0.5}}
    assert Theme.model_validate(theme(variant=[variant(light=gentle)])).variant[0].light is not None


def test_the_fixture_theme_has_variants_with_backdrops_and_stays_readable():
    (acme,) = themes_in(Path(__file__).parents[2] / "fixtures" / "layers" / "heinzel_acme" / "themes")
    assert len(acme.variant) == 3
    night_rain = acme.colours("dark", {"daypart": "night", "weather": "rain"})
    assert night_rain.backdrop is not None
    assert night_rain.backdrop.gradient == "radial"
    assert acme.colours("light", {"daypart": "evening"}).main_tint == "#eef0f7"


def test_the_weather_theme_of_the_guide_is_valid_and_readable():
    guide = (Path(__file__).parents[3] / "docs" / "THEMES.md").read_text(encoding="utf-8")
    (example,) = re.findall(r"```toml\n(.*?)```", guide, flags=re.DOTALL)
    weather = Theme.model_validate(tomllib.loads(example))
    assert len(weather.variant) == 6
    assert weather.colours("dark", {"daypart": "night", "weather": "rain"}).background == "#0d1016"
    assert weather.colours("light", {"daypart": "day", "weather": "clear"}).main_tint == "#e0f2fe"
