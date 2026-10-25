# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Draw Heinzelmännchen for the terminal: `make terminal-art` writes src/heinzel/heads/cli/_art_data.py.

The drawing is web/src/components/heinzel.ts (node runs it as it is), so the terminal shows the same Heinzelmännchen as
the web app. rsvg-convert rasterises its SVG, the pixels are reduced to a 14 colour palette by median cut and written as
rows of palette indices; the CLI turns two pixel rows into one row of half blocks at run time, in whatever colour depth
the terminal has. The result is committed, because the build has no rasteriser; the same drawing and tools give the same
file, so a changed drawing shows as a changed file.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRAWING = ROOT / "web/src/components/heinzel.ts"
OUT = ROOT / "src/heinzel/heads/cli/_art_data.py"
COLOURS = 14
TIMEOUT = 120
DEPTH = ("-depth", "8", "rgba:-")
OPAQUE = 128
"""The alpha from which a pixel counts as drawn."""
SPLITTABLE = 2
BUST = 24
"""The bust's side in pixels; two pixel rows make one terminal row, so it is 24 columns by 12 rows."""
SWARM = (40, 20)
"""The swarm's size in pixels: 40 columns by 10 rows."""

NODE = """
import { heinzelSvg, heinzelSwarmSvg } from %s;
const [what] = process.argv.slice(1);
process.stdout.write(what === "swarm" ? heinzelSwarmSvg() : heinzelSvg({}, "bust"));
"""


def svg(what: str) -> str:
    """The SVG of `what` ("bust" or "swarm") from the drawing."""
    code = NODE % json.dumps(DRAWING.as_uri())
    run = subprocess.run(  # noqa: S603 - fixed arguments
        [shutil.which("node") or "node", "--input-type=module", "-e", code, what],
        capture_output=True, text=True, check=True, timeout=TIMEOUT,
    )  # fmt: skip
    return run.stdout


def pixels(source: str, width: int, height: int) -> list[list[tuple[int, int, int] | None]]:
    """Rasterise `source` to width by height; None where it is mostly transparent."""
    big = 8
    png = subprocess.run(  # noqa: S603 - fixed arguments
        [shutil.which("rsvg-convert") or "rsvg-convert", "-w", str(width * big), "-h", str(height * big)],
        input=source.encode(), capture_output=True, check=True, timeout=TIMEOUT,
    ).stdout  # fmt: skip
    raw = subprocess.run(  # noqa: S603 - fixed arguments
        [shutil.which("magick") or "magick", "png:-", "-filter", "box", "-resize", f"{width}x{height}!", *DEPTH],
        input=png, capture_output=True, check=True, timeout=TIMEOUT,
    ).stdout  # fmt: skip
    rows: list[list[tuple[int, int, int] | None]] = []
    for y in range(height):
        row: list[tuple[int, int, int] | None] = []
        for x in range(width):
            r, g, b, a = raw[(y * width + x) * 4 : (y * width + x) * 4 + 4]
            row.append((r, g, b) if a >= OPAQUE else None)
        rows.append(row)
    return rows


def palette_of(grids: list[list[list[tuple[int, int, int] | None]]]) -> list[tuple[int, int, int]]:
    """A palette for all grids by median cut."""
    boxes = [[p for grid in grids for row in grid for p in row if p is not None]]
    while len(boxes) < COLOURS:
        box = max(boxes, key=lambda b: len(set(b)) * 1_000_000 + len(b))
        if len(set(box)) < SPLITTABLE:
            break
        boxes.remove(box)
        channel = max(range(3), key=lambda c: max(p[c] for p in box) - min(p[c] for p in box))
        box.sort(key=lambda p: (p[channel], p))
        half = len(box) // 2
        boxes += [box[:half], box[half:]]
    return sorted({tuple(round(sum(p[c] for p in b) / len(b)) for c in range(3)) for b in boxes})  # type: ignore[misc]


def indexed(grid: list[list[tuple[int, int, int] | None]], palette: list[tuple[int, int, int]]) -> list[str]:
    """Rows of palette indices as hex digits, "." for transparent."""

    def nearest(p: tuple[int, int, int]) -> str:
        distance = [sum((a - b) ** 2 for a, b in zip(p, colour, strict=True)) for colour in palette]
        return f"{distance.index(min(distance)):x}"

    return ["".join("." if p is None else nearest(p) for p in row) for row in grid]


def main() -> None:
    """Write the data file."""
    if not (shutil.which("node") and shutil.which("rsvg-convert") and shutil.which("magick")):
        sys.exit("terminal-art needs node, rsvg-convert (librsvg) and magick (ImageMagick) on PATH.")
    bust = pixels(svg("bust"), BUST, BUST)
    swarm = pixels(svg("swarm"), *SWARM)
    palette = palette_of([bust, swarm])
    lines = [
        '"""Heinzelmännchen as pixels for the terminal, made by scripts/terminal_art.py (`make terminal-art`).',
        "",
        "Rows of palette indices (hex digits, `.` is transparent); two rows make one row of half blocks.",
        '"""',
        "",
        "PALETTE: tuple[tuple[int, int, int], ...] = (",
        *[f"    {colour!r}," for colour in palette],
        ")",
        "",
        "BUST: tuple[str, ...] = (",
        *[f"    {row!r}," for row in indexed(bust, palette)],
        ")",
        "",
        "SWARM: tuple[str, ...] = (",
        *[f"    {row!r}," for row in indexed(swarm, palette)],
        ")",
        "",
    ]
    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
