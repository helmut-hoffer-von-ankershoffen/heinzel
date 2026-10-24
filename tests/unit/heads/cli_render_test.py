import json

import pytest
from pydantic import BaseModel
from rich.console import Console

from heinzel.heads.cli._render import show, to_json

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


def test_to_json():
    assert json.loads(to_json([Row(name="a", tags=[], size=1)])) == [{"name": "a", "tags": [], "size": 1}]
