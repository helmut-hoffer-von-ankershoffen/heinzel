"""How `hz org show` and `hz org agenda` look in a terminal."""

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from heinzel import Event
from heinzel.modules.org._agenda import Agenda
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


def render_agenda(agenda: Agenda) -> RenderableType:
    """The meeting now and the next one, one line each, or why they can't be read and what to run."""
    if agenda.problem is not None:
        fix = f" Run `{agenda.fix}`." if agenda.fix else ""
        return Text(f"{agenda.problem}{fix}")
    now = Text("Now:  ")
    now.append(_event(agenda.current, agenda, "until") if agenda.current else "free")
    upcoming = Text("Next: ")
    upcoming.append(_event(agenda.next, agenda, "at") if agenda.next else "nothing until the end of tomorrow")
    return Group(now, upcoming)


def _event(event: Event, agenda: Agenda, word: str) -> str:
    when = (event.end if word == "until" else event.start).astimezone(agenda.now.tzinfo)
    day = "" if when.date() == agenda.now.date() else f"{when:%a} "
    join = f" ({event.conference_url})" if event.conference_url else ""
    return f"{event.title}, {word} {day}{when:%H:%M}{join}"
