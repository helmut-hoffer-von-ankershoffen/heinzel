"""How `hz weather` looks in a terminal. Text, never markup: the summaries come from the adapter."""

from rich.console import Group
from rich.table import Table
from rich.text import Text

from heinzel.modules.weather._models import Outlook


def render_outlook(outlook: Outlook) -> Group:
    """The weather now in a line, then a table of the next days."""
    unit = outlook.units.degree
    rain = (
        f", {outlook.precipitation:g} {outlook.units.length} of rain in the last hour" if outlook.precipitation else ""
    )
    now = Text(
        f"{outlook.location}: {outlook.temperature:.0f}{unit}, {outlook.summary} "
        f"(feels like {outlook.feels_like:.0f}{unit}; wind {outlook.wind:.0f} {outlook.units.speed}{rain})"
    )
    table = Table(title="Next days", title_justify="left")
    for column in ("Day", "Low", "High", "Rain", "Sky"):
        table.add_column(column)
    for day in outlook.days:
        chance = "" if day.rain_chance is None else f"{round(day.rain_chance * 100)}%"
        table.add_row(
            Text(day.day.strftime("%a %d %b")),
            Text(f"{day.low:.0f}{unit}"),
            Text(f"{day.high:.0f}{unit}"),
            Text(chance),
            Text(day.summary),
        )
    return Group(now, table)
