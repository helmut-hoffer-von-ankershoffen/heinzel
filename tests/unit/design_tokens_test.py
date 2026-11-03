"""The design tokens can't drift: app CSS, default theme, avatar palette and the Pages site follow them."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "design_tokens.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("design_tokens", SCRIPT)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


design_tokens = _load()
DATA = design_tokens.load()


def test_app_css_default_theme_avatar_site_and_badges_follow_the_tokens():
    """One call to the same checks `make lint` runs, so a drifting value fails with the exact line."""
    assert design_tokens.problems_found() == []


def test_the_generated_site_stylesheet_is_current():
    assert design_tokens.check_generated(DATA) == []


def test_the_site_stylesheets_keep_no_colour_literal():
    assert design_tokens.check_site_styles() == []


def test_a_zero_alpha_colour_equals_transparent():
    assert design_tokens.normal("transparent") == design_tokens.normal("#ffffff00")
    assert design_tokens.normal("#18181800") == design_tokens.normal("#FFF0")
    assert design_tokens.normal("#B4532F") == "#b4532f"


def test_only_black_with_an_alpha_is_allowed_as_a_literal():
    assert design_tokens.literal_colours("a{box-shadow:0 1px #0002;b:#00000012;c:rgb(0 0 0 / 75%)}") == []
    assert design_tokens.literal_colours("a{color:#fff}") == ["#fff"]
    assert design_tokens.literal_colours("a{color:rgb(1 2 3)} /* #123456 */") == ["rgb(1 2 3)"]


def test_scalars_match_the_documented_values():
    assert (
        design_tokens.scalars(DATA).items()
        >= {
            "--radius-lg": "0.5rem",
            "--radius-xl": "0.75rem",
            "--text-ui": "15px",
            "--text-ui-sm": "13px",
            "--icon": "18px",
            "--row": "36px",
        }.items()
    )
