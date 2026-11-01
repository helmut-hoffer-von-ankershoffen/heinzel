"""JSON-ready forms of values that must be serialisable: a value that isn't fails, it is never turned into text."""

from __future__ import annotations

from typing import Any

from pydantic_core import PydanticSerializationError, to_jsonable_python


def _refuse(value: object) -> object:
    """Fail on a value with no JSON form.

    Raises:
        PydanticSerializationError: Always.
    """
    msg = f"Unable to serialize unknown type: {type(value)}"
    raise PydanticSerializationError(msg)


def jsonable(value: Any) -> Any:  # noqa: ANN401 - any value in, its JSON form out
    """`value` as JSON-ready data.

    Raises:
        PydanticSerializationError: If it holds a value with no JSON form; nothing is stringified in its place.
    """
    return to_jsonable_python(value, fallback=_refuse)
