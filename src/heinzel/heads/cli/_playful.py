"""A small Heinzelmännchen in the terminal: the start screen's bust, the setup welcome's swarm and one-line touches.

The pixels come from the same drawing as the web app (`make terminal-art`). Art is a garnish: it appears only where a
person watches a colour terminal, never replaces information, and a Rich console downgrades its colours to 256 or 16
when the terminal has no more. At most one block of art per command: the start screen and `--version` show the bust,
`hz setup` the swarm, nothing else more than a line.
"""

import os
from collections.abc import Mapping, Sequence

from rich.console import Console
from rich.style import Style
from rich.text import Text

from heinzel.heads.cli._art_data import BUST, PALETTE, SWARM
from heinzel.kernel import Settings

HAT = Style(color="rgb(224,84,41)")
"""The default Heinzelmännchen's hat."""
FACE = Style(color="rgb(245,199,86)")
WALK_FRAMES = 4


def allowed(settings: Settings, console: Console, environ: Mapping[str, str] | None = None) -> bool:
    """Whether art may appear on `console`: colour terminal, setting on, text output, no NO_COLOR, CI or dumb TERM."""
    env = os.environ if environ is None else environ
    return bool(
        settings.playful
        and settings.output_format != "json"
        and console.is_terminal
        and console.color_system is not None
        and not console.is_dumb_terminal
        and not env.get("NO_COLOR")
        and not env.get("CI")
        and env.get("TERM") != "dumb"
    )


def pixels(rows: Sequence[str]) -> Text:
    """Pixel rows (palette indices) as half-block text: two pixel rows make one line."""
    lines: list[Text] = []
    padded = [*rows, "." * len(rows[0])] if len(rows) % 2 else list(rows)
    for top, bottom in zip(padded[::2], padded[1::2], strict=True):
        line = Text()
        for upper, lower in zip(top, bottom, strict=True):
            line.append(*_cell(upper, lower))
        lines.append(line)
    return Text("\n").join(lines)


def _cell(upper: str, lower: str) -> tuple[str, Style | None]:
    def rgb(index: str) -> str:
        r, g, b = PALETTE[int(index, 16)]
        return f"rgb({r},{g},{b})"

    if upper == lower == ".":
        return " ", None
    if lower == ".":
        return "▀", Style(color=rgb(upper))
    if upper == ".":
        return "▄", Style(color=rgb(lower))
    if upper == lower:
        return "█", Style(color=rgb(upper))
    return "▀", Style(color=rgb(upper), bgcolor=rgb(lower))


def banner(lines: Sequence[str]) -> Text:
    """The bust with `lines` of text beside its lower half; 80 columns at most."""
    art = pixels(BUST).split("\n")
    width = max(len(line) for line in art)
    start = len(art) // 2 - len(lines) // 2
    out = Text()
    for number, line in enumerate(art):
        out.append_text(line)
        out.pad_right(width - len(line) + 2)
        if 0 <= (at := number - start) < len(lines):
            out.append(lines[at], style="bold magenta" if at == 0 else "dim")
        out.append("\n")
    return out


def swarm() -> Text:
    """The swarm, a busy group walking together."""
    return pixels(SWARM)


def glyph() -> Text:
    """A Heinzelmännchen in one cell: its hat over its face."""
    return Text("▀", style=Style(color=HAT.color, bgcolor=FACE.color))


def walking(seconds: float) -> Text:
    """One frame of a Heinzelmännchen walking along a path of six cells, a new frame every eighth of a second."""
    frame = int(seconds * 8) % (WALK_FRAMES * 2)
    step = frame if frame < WALK_FRAMES else WALK_FRAMES * 2 - frame - 1
    legs = "▙" if frame % 2 == 0 else "▟"
    out = Text(" " * step)
    out.append_text(glyph())
    out.append(legs, style=Style(color="rgb(30,136,229)"))
    out.append(" " * (WALK_FRAMES - step))
    return out
