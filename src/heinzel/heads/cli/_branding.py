"""Text-only version footer for Typer help output."""

from heinzel import __version__

TAGLINE = "helpers accelerating product teams"


def footer(width: int) -> str:
    """Show only the version; width is retained for API compatibility."""
    del width
    return f"[dim]v{__version__}[/dim]"
