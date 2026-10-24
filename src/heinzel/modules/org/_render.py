"""How `hz org show` looks in a terminal."""

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from heinzel.modules.org._models import Profile


def render_profile(profile: Profile) -> RenderableType:
    """The profile as a table of settings, and what to run when something is missing."""
    table = Table(title="Your profile", show_header=False)
    table.add_column("Setting")
    table.add_column("Value", overflow="fold")
    rows = {
        "handle": profile.handle,
        "role": profile.role,
        "time zone": profile.timezone,
        "working hours": profile.working_hours if profile.working_hours_set else f"{profile.working_hours} (default)",
        "GitHub login": profile.github,
        "coding agent": profile.coding_agent,
        "name": profile.name,
        "email": profile.email,
    }
    for name, value in rows.items():
        table.add_row(name, Text(value or "not set"))
    if profile.complete:
        return table
    return Group(table, Text(f"Missing {', '.join(profile.missing)}: run `{profile.fix}`."))
