"""The public heads use the original character rather than a generic elf emoji (ADR 0071)."""

import ast
from pathlib import Path

import pytest
from rich.text import Text

from heinzel import __version__
from heinzel.heads.cli._branding import TAGLINE, footer

pytestmark = pytest.mark.unit
HEADS = Path(__file__).resolve().parents[3] / "src" / "heinzel" / "heads"


def test_heads_do_not_reintroduce_the_generic_elf_emoji():
    found = [
        str(path.relative_to(HEADS))
        for path in HEADS.rglob("*.py")
        if any(
            isinstance(node, ast.Constant) and isinstance(node.value, str) and "\N{ELF}" in node.value
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
    ]
    assert not found, f"Use original artwork or plain text branding, not the generic elf emoji: {found}"


@pytest.mark.parametrize("width", [20, 40, 80, 140])
def test_footer_shows_only_the_version(width):
    rendered = Text.from_markup(footer(width)).plain
    assert rendered == f"v{__version__}"
    assert TAGLINE not in rendered
    assert "built with AI in Berlin" not in rendered
    assert "hz " not in rendered
