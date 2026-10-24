"""Default presentation of operation results on a terminal.

Values are text, never Rich markup: a value such as `[capabilities] code_host = ...` shows as written.
"""

from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel
from pydantic_core import to_jsonable_python
from rich.console import Console
from rich.json import JSON
from rich.table import Table
from rich.text import Text


def to_json(result: object) -> str:
    """The JSON form of a result, as every machine-facing head returns it."""
    return JSON.from_data(to_jsonable_python(result)).text.plain


def show(console: Console, result: object) -> None:
    """Print a result without a module-specific renderer: models as tables, everything else as JSON."""
    if result is None:
        return
    if isinstance(result, str | Path):
        console.print(str(result), markup=False, highlight=False, soft_wrap=True)
    elif isinstance(result, BaseModel):
        console.print(_fields_table(result.model_dump(mode="json")))
    elif isinstance(result, list) and (items := cast("list[object]", result)) and _all_models(items):
        console.print(_rows_table([cast("BaseModel", item).model_dump(mode="json") for item in items]))
    elif isinstance(result, list) and not result:
        console.print("[dim]Nothing to show.[/dim]")
    else:
        console.print_json(data=to_jsonable_python(result))


def _all_models(items: list[object]) -> bool:
    return all(isinstance(item, BaseModel) for item in items)


def _cell(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        items = cast("list[object]", value)
        return "\n".join(_cell(item) for item in items)
    return JSON.from_data(value).text.plain


def _fields_table(data: dict[str, Any]) -> Table:
    table = Table(show_header=False, box=None)
    table.add_column(style="bold cyan", overflow="fold")
    table.add_column(overflow="fold")
    for key, value in data.items():
        table.add_row(Text(key), Text(_cell(value)))
    return table


def _rows_table(rows: list[dict[str, Any]]) -> Table:
    table = Table()
    for column in rows[0]:
        table.add_column(column.replace("_", " ").capitalize(), overflow="fold")
    for row in rows:
        table.add_row(*(Text(_cell(value)) for value in row.values()))
    return table
