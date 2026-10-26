"""Text-only footer branding, sized for Typer's one-cell help padding (ADR 0071)."""

from rich.console import Console
from rich.text import Text

from heinzel import __version__

TAGLINE = "helpers accelerating product teams"
ATTRIBUTION = "🐻 built with AI in Berlin"
MIN_GAP = 2


def footer(width: int) -> str:
    """The version at left and attribution at right, wrapping within a narrow terminal."""
    available = max(1, width - 2)
    version = f"v{__version__}"
    gap = available - Text(version).cell_len - Text(ATTRIBUTION).cell_len
    if gap >= MIN_GAP:
        return f"[dim]{version}{' ' * gap}{ATTRIBUTION}[/dim]"
    console = Console(width=available)
    lines = [line.plain for line in Text(version).wrap(console, available)]
    lines.extend(" " * (available - line.cell_len) + line.plain for line in Text(ATTRIBUTION).wrap(console, available))
    # Each line starts with markup so Typer's cleandoc cannot strip the right-alignment spaces.
    return "\n".join(f"[dim]{line}[/dim]" for line in lines)
