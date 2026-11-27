"""Compatibility path for the kernel's JSON helpers."""

from typing import Any

from pydantic_core import PydanticSerializationError, to_jsonable_python

from heinzel.spec._json import _refuse as _published_refuse
from heinzel.spec._json import jsonable

__all__ = ["Any", "PydanticSerializationError", "jsonable", "to_jsonable_python"]

_refuse = _published_refuse
