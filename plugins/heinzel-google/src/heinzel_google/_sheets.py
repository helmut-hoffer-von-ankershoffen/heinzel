"""A Google Sheets spreadsheet (the Sheets API's JSON with grid data) to Markdown-flavoured plain text.

Each sheet becomes a `## <title>` heading and a pipe table of the cells' formatted values, as people see them; the
first row is the table's header. Formulas, notes, formatting and charts are left out; a chart sheet is named, not
drawn. The output is bounded like a document's: `MAX_CELLS` cells, and `max_output(size)` characters in all.
"""

from typing import Any, cast

from heinzel import HeinzelError, clean_text
from heinzel_google._docs import max_output

MAX_CELLS = 1_000_000
"""Cells read from one spreadsheet, empty ones included."""

type Json = dict[str, Any]


def _objects(value: object) -> list[Json]:
    if not isinstance(value, list):
        return []
    return [cast("Json", item) for item in cast("list[object]", value) if isinstance(item, dict)]


def _object(value: object) -> Json:
    return cast("Json", value) if isinstance(value, dict) else {}


def _cell(value: Json) -> str:
    text = value.get("formattedValue")
    return " ".join(str(text).split()).replace("|", "\\|") if text is not None else ""


def _rows(sheet: Json, cells: int) -> tuple[list[list[str]], int]:
    """The sheet's rows without trailing empty cells and rows, and the cells counted so far, all sheets together.

    Raises:
        HeinzelError: If the count passes `MAX_CELLS`.
    """
    rows: list[list[str]] = []
    for grid in _objects(sheet.get("data")):
        for row in _objects(grid.get("rowData")):
            values = _objects(row.get("values"))
            cells += len(values)
            if cells > MAX_CELLS:
                msg = f"The spreadsheet has more than {MAX_CELLS:,} cells; it is too large to read."
                raise HeinzelError(msg)
            found = [_cell(value) for value in values]
            while found and not found[-1]:
                found.pop()
            rows.append(found)
    while rows and not any(rows[-1]):
        rows.pop()
    return rows, cells


def _table(title: str, rows: list[list[str]], *, chart: bool) -> list[str]:
    """The lines of a sheet's pipe table, its first row the header.

    Raises:
        HeinzelError: If padding short rows to the widest one would pass `MAX_CELLS`.
    """
    if chart:
        return ["(a chart, not read)"]
    if not rows:
        return ["(empty)"]
    width = max(len(row) for row in rows)
    if len(rows) * width > MAX_CELLS:
        msg = f"The sheet {title!r} spans more than {MAX_CELLS:,} cells; it is too large to read."
        raise HeinzelError(msg)
    lines = []
    for number, row in enumerate(rows):
        lines.append("| " + " | ".join(row + [""] * (width - len(row))) + " |")
        if number == 0:
            lines.append("|" + " --- |" * width)
    return lines


def spreadsheet_to_text(spreadsheet: Json, size: int) -> str:
    """The text of a Sheets API spreadsheet read with `includeGridData`, sheet by sheet.

    Args:
        spreadsheet: The answer of `spreadsheets.get`.
        size: Bytes of JSON it was parsed from, which bounds the output (`max_output`).

    Raises:
        HeinzelError: If it has more than `MAX_CELLS` cells, or its text would pass `max_output(size)` characters.
    """
    limit, written, cells = max_output(size), 0, 0
    blocks: list[str] = []
    for sheet in _objects(spreadsheet.get("sheets")):
        properties = _object(sheet.get("properties"))
        title = " ".join(clean_text(str(properties.get("title") or "Untitled sheet")).split())
        rows, cells = _rows(sheet, cells)
        block = "\n".join([f"## {title}", *_table(title, rows, chart=properties.get("sheetType") == "OBJECT")])
        written += len(block)
        if written > limit:
            msg = f"The spreadsheet's text would pass {limit:,} characters; it is too large to read."
            raise HeinzelError(msg)
        blocks.append(block)
    return "\n\n".join(blocks).strip() + "\n"
