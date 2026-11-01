"""Default presentation of operation results on a terminal, and what keeps every printed value terminal-safe.

Values are text, never Rich markup: a value such as `[capabilities] code_host = ...` shows as written. And no value
reaches the terminal with its control characters: configuration, plugins and contract files can hold escape sequences
(colours, OSC 8 links, OSC 52 clipboard writes, C1 controls such as CSI, U+009B) that a terminal would act on.
"""

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel
from pydantic_core import PydanticSerializationError, to_jsonable_python
from rich.console import Console, ConsoleOptions, RenderableType
from rich.json import JSON
from rich.segment import Segment
from rich.table import Table
from rich.text import Text

CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
"""C0 controls but tab and line feed, DEL and C1 controls: what a terminal acts on rather than shows."""


def terminal_safe(text: str) -> str:
    """`text` without control characters other than tab and line feed.

    They are removed rather than shown as escapes: Rich gives them no width, so removing them keeps tables aligned.
    """
    return CONTROLS.sub("", text)


class TerminalSafe:
    """A renderable printed without control characters in its text; Rich's own styling is kept.

    Wraps whatever a head prints, module renderers included, so no module has to remember it: the text of every
    segment is cleaned after Rich lays it out and before the console writes its own escape codes.
    """

    def __init__(self, renderable: RenderableType) -> None:
        """Wrap `renderable`."""
        self.renderable = renderable

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> Iterable[Segment]:
        """Render the wrapped renderable.

        Yields:
            Its segments, with their text terminal-safe; control segments (Rich's own) unchanged.
        """
        for segment in console.render(self.renderable, options):
            yield segment if segment.control else Segment(terminal_safe(segment.text), segment.style)


def message(label: str, text: str) -> TerminalSafe:
    """An error or warning line: a red label, then `text` as written, terminal-safe."""
    return TerminalSafe(Text.assemble((label, "red"), " ", text))


def _refuse(value: object) -> object:
    """Fail on a value with no JSON form, as `to_jsonable_python` does by default, rather than stringify it.

    Raises:
        PydanticSerializationError: Always.
    """
    msg = f"Unable to serialize unknown type: {type(value)}"
    raise PydanticSerializationError(msg)


def to_json(result: object) -> str:
    """The JSON form of a result, as every machine-facing head returns it; C1 controls are escaped like C0 ones."""
    text = JSON.from_data(to_jsonable_python(result, fallback=_refuse)).text.plain
    return re.sub(r"[\x7f-\x9f]", lambda match: f"\\u{ord(match.group()):04x}", text)


def show(console: Console, result: object) -> None:
    """Print a result without a module-specific renderer: models as tables, everything else as JSON."""
    if result is None:
        return
    if isinstance(result, str | Path):
        console.print(TerminalSafe(Text(str(result))), highlight=False, soft_wrap=True)
    elif isinstance(result, BaseModel):
        console.print(TerminalSafe(_fields_table(result.model_dump(mode="json"))))
    elif isinstance(result, list) and (items := cast("list[object]", result)) and _all_models(items):
        console.print(TerminalSafe(_rows_table([cast("BaseModel", item).model_dump(mode="json") for item in items])))
    elif isinstance(result, list) and not result:
        console.print("[dim]Nothing to show.[/dim]")
    else:
        text = to_json(result)  # pyright: ignore[reportUnknownArgumentType] - any JSON value
        console.print(TerminalSafe(Text(text)), highlight=False, soft_wrap=True)


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
