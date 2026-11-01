"""A value that must be JSON-ready fails when it has no JSON form; it is never turned into text instead."""

from datetime import date

import pytest
from pydantic_core import PydanticSerializationError

from heinzel.heads.cli._render import _refuse as refuse_render
from heinzel.modules.heinzel._jsonable import jsonable
from heinzel.modules.system.config._service import _refuse as refuse_config
from heinzel.modules.system.plugin.docs._service import _refuse as refuse_docs

pytestmark = pytest.mark.unit


def test_values_with_a_json_form_are_converted():
    assert jsonable({"when": date(2026, 11, 1), "tags": ("a", "b")}) == {"when": "2026-11-01", "tags": ["a", "b"]}


@pytest.mark.parametrize(
    "value", [object(), {"nested": [object()]}, {"a": {1, object()}}], ids=["bare", "nested", "set"]
)
def test_a_value_without_a_json_form_fails_instead_of_becoming_text(value):
    with pytest.raises(PydanticSerializationError, match="Unable to serialize unknown type"):
        jsonable(value)


@pytest.mark.parametrize("refuse", [refuse_config, refuse_docs, refuse_render], ids=["config", "docs", "cli"])
def test_every_refusing_fallback_raises_the_error_the_default_raises(refuse):
    with pytest.raises(PydanticSerializationError, match="Unable to serialize unknown type: <class 'object'>"):
        refuse(object())
