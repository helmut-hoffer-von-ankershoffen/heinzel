"""Themes as data: every value is checked before it can become a CSS custom property (ADR 0044)."""

import pytest
from pydantic import ValidationError

from heinzel import Module, Theme, ThemeColours, ThemeLet
from heinzel.kernel import DEFAULT_THEME, font_stack_problem
from heinzel.kernel._themes import themes_in

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
    assert let == ThemeLet(name="acme", theme=let.theme)
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
