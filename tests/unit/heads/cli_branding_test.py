"""The public heads use the original character rather than a generic elf emoji (ADR 0071)."""

import ast
import io
from pathlib import Path

import pytest
from rich.console import Console
from rich.padding import Padding
from rich.text import Text

from heinzel import __version__
from heinzel.heads.cli._branding import ATTRIBUTION, TAGLINE, footer

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
def test_footer_keeps_version_and_right_aligned_credits_within_terminal(width):
    stream = io.StringIO()
    console = Console(file=stream, width=width, color_system=None)
    console.print(Padding(Text.from_markup(footer(width)), (0, 1)))
    lines = stream.getvalue().splitlines()
    assert f"v{__version__}" in stream.getvalue()
    assert "built with AI in Berlin" in " ".join(stream.getvalue().split())
    assert TAGLINE not in stream.getvalue()
    assert "hz " not in stream.getvalue()
    assert all(Text(line).cell_len <= width for line in lines)
    assert Text(next(line.rstrip() for line in lines if "Berlin" in line)).cell_len == width - 1
    if width >= 40:
        assert len(lines) == 1
        assert ATTRIBUTION in lines[0]
