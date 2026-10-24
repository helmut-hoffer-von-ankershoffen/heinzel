"""Themes as data: every value is checked before it can become a CSS custom property (ADR 0044)."""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from heinzel import Module, Theme, ThemeColours, ThemeFont, ThemeFonts, ThemeLet
from heinzel.kernel import DEFAULT_THEME, FONT_ROUTE, font_stack_problem
from heinzel.kernel import _themes as themes
from heinzel.kernel._themes import MAX_FONT_BYTES, font_file, read_font, themes_in

pytestmark = pytest.mark.unit

COLOURS = DEFAULT_THEME.light.model_dump()


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
