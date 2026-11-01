"""JSON-ready forms of values that must be serialisable."""

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
    """`value` as JSON-ready data (dates as ISO text, models as dicts, tuples and sets as lists).

    A value with no JSON form fails; it is never turned into text in its place, so a bug can't leak a repr into a
    document, a digest or a config file.

    Raises:
        PydanticSerializationError: If `value` holds a value with no JSON form.
    """
    return to_jsonable_python(value, fallback=_refuse)
