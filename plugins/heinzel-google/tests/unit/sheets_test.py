"""The Sheets JSON to text converter."""

import pytest
from heinzel_google import _sheets
from heinzel_google._sheets import spreadsheet_to_text

from heinzel import HeinzelError

pytestmark = pytest.mark.unit


def sheet(title: object, *rows: list[object], kind: str = "GRID") -> dict[str, object]:
    data = [{"rowData": [{"values": [{} if v is None else {"formattedValue": v} for v in row]} for row in rows]}]
    return {"properties": {"title": title, "sheetType": kind}, "data": data}


def test_each_sheet_is_a_table_with_formatted_values():
    spreadsheet = {
        "sheets": [
            sheet("Budget\u202e", ["Item", "Cost", None], ["Ads | print", "1,200 €"], ["Rent\nmonthly"], [None, ""]),
            sheet("Chart", kind="OBJECT"),
            sheet(None),
            {"properties": {"title": "No data"}},
        ]
    }
    assert spreadsheet_to_text(spreadsheet, 500) == (
        "## Budget\n| Item | Cost |\n| --- | --- |\n| Ads \\| print | 1,200 € |\n| Rent monthly |  |\n\n"
        "## Chart\n(a chart, not read)\n\n## Untitled sheet\n(empty)\n\n## No data\n(empty)\n"
    )


def test_nothing_is_an_empty_line():
    assert spreadsheet_to_text({}, 2) == "\n"


def test_too_many_cells_are_refused(monkeypatch):
    monkeypatch.setattr(_sheets, "MAX_CELLS", 3)
    with pytest.raises(HeinzelError, match="more than 3 cells"):
        spreadsheet_to_text({"sheets": [sheet("A", ["1", "2"], ["3", "4"])]}, 100)


def test_padding_short_rows_is_bounded(monkeypatch):
    monkeypatch.setattr(_sheets, "MAX_CELLS", 5)
    with pytest.raises(HeinzelError, match="spans more than 5 cells"):
        spreadsheet_to_text({"sheets": [sheet("A", ["1", "2", "3"], ["4"], ["5"])]}, 100)


def test_the_output_is_bounded(monkeypatch):
    monkeypatch.setattr(_sheets, "max_output", lambda size: 10)
    with pytest.raises(HeinzelError, match="would pass 10 characters"):
        spreadsheet_to_text({"sheets": [sheet("A long title", ["1"])]}, 100)
