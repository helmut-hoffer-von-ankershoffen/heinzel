"""The JSON Schema of each setting, self-contained: what a form needs to edit it without knowing pydantic."""

import warnings
from typing import Any, cast

from pydantic.errors import PydanticInvalidForJsonSchema, PydanticUserError
from pydantic.json_schema import PydanticJsonSchemaWarning

from heinzel import ModuleSettings

_PREFIX = "#/$defs/"


def setting_schemas(model: type[ModuleSettings]) -> dict[str, dict[str, Any]]:
    """Each field's JSON Schema, with every `$ref` resolved inline; empty when the model has no JSON Schema."""
    try:
        with warnings.catch_warnings():
            # A default JSON can't hold is left out of the schema; `default` on the setting still says what it is.
            warnings.simplefilter("ignore", PydanticJsonSchemaWarning)
            schema = model.model_json_schema()
    except (PydanticInvalidForJsonSchema, PydanticUserError):
        return {}  # a plugin's setting of a type JSON Schema can't describe: the form falls back to text
    definitions = cast("dict[str, Any]", schema.get("$defs", {}))
    properties = cast("dict[str, Any]", schema.get("properties", {}))
    return {name: _inline(node, definitions, frozenset()) for name, node in properties.items()}


def _inline(node: object, definitions: dict[str, Any], seen: frozenset[str]) -> Any:  # noqa: ANN401 - free-form JSON Schema
    """`node` with each `$ref` replaced by its definition; a definition that contains itself becomes `{}`."""
    if isinstance(node, list):
        return [_inline(item, definitions, seen) for item in cast("list[object]", node)]
    if not isinstance(node, dict):
        return node
    table = cast("dict[str, Any]", node)
    reference = table.get("$ref")
    siblings = {key: _inline(value, definitions, seen) for key, value in table.items() if key != "$ref"}
    if not isinstance(reference, str) or not reference.startswith(_PREFIX):
        return siblings
    name = reference.removeprefix(_PREFIX)
    if name in seen or name not in definitions:
        return siblings  # a recursive model: describing it once more would never end
    return {**_inline(definitions[name], definitions, seen | {name}), **siblings}
