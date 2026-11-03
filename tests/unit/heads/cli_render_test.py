import io
import json

import pytest
from pydantic import BaseModel
from pydantic_core import PydanticSerializationError
from rich.console import Console
from rich.table import Table
from rich.text import Text

from heinzel.heads.cli._render import CONTROLS, TerminalSafe, message, show, terminal_safe, to_json

pytestmark = pytest.mark.unit


class Row(BaseModel):
    name: str
    tags: list[str]
    size: int


def render(result: object) -> str:
    console = Console(record=True, width=200)
    show(console, result)
    return console.export_text()


def test_none_prints_nothing():
    assert render(None) == ""


def test_strings_print_verbatim():
    assert render("[bold]x[/bold]").strip() == "[bold]x[/bold]"


def test_model_prints_fields():
    output = render(Row(name="a", tags=["x", "y"], size=3))
    assert "name" in output
    assert "x" in output
    assert "y" in output


def test_list_of_models_prints_rows():
    output = render([Row(name="a", tags=[], size=1), Row(name="b", tags=["t"], size=2)])
    assert "Name" in output
    assert "b" in output


def test_empty_list():
    assert "Nothing to show" in render([])


def test_other_values_print_as_json():
    assert json.loads(render({"a": 1})) == {"a": 1}


def test_to_json_fails_on_a_value_with_no_json_form_instead_of_printing_its_repr():
    with pytest.raises(PydanticSerializationError):
        to_json({"value": object()})


def test_to_json():
    assert json.loads(to_json([Row(name="a", tags=[], size=1)])) == [{"name": "a", "tags": [], "size": 1}]


ESCAPES = "\x1b[31mred\x1b[0m \x1b]8;;https://evil.invalid\x1b\\link\x1b]8;;\x1b\\ \x1b]52;c;eA==\x07 \x9b2J"


def written(renderable: object, *, colour: bool = False) -> str:
    """What the console writes; without colour, any control character came from the value."""
    console = Console(file=io.StringIO(), width=120, color_system="standard" if colour else None, force_terminal=colour)
    console.print(renderable)
    return console.file.getvalue()


def controls(text: str) -> list[str]:
    return CONTROLS.findall(text.replace("\n", ""))


def test_terminal_safe_strips_controls_but_tabs_and_newlines():
    assert terminal_safe("a\tb\nc\x1b[31m\x9b\x07\x7f\x00d") == "a\tb\nc[31md"


@pytest.mark.parametrize(
    "result",
    [ESCAPES, Row(name=ESCAPES, tags=[ESCAPES], size=1), [Row(name=ESCAPES, tags=[], size=1)], {"k": ESCAPES}],
)
def test_default_rendering_writes_no_control_characters(result):
    console = Console(file=io.StringIO(), width=120, color_system=None)
    show(console, result)
    assert controls(console.file.getvalue()) == []


def test_any_wrapped_renderable_is_safe_and_keeps_rich_styles():
    table = Table("Title")
    table.add_row(ESCAPES)
    assert controls(written(Text(ESCAPES)))  # Rich alone lets them through
    assert controls(written(TerminalSafe(table))) == []
    assert controls(written(message("Error:", ESCAPES))) == []
    assert "\x1b[31m" in written(message("Error:", "plain"), colour=True)  # Rich's own styling survives


def test_json_escapes_c1_controls_too():
    assert to_json({"x": "\x9b\x1b"}) == '{\n  "x": "\\u009b\\u001b"\n}'
