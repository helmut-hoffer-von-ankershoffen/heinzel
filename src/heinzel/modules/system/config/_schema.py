"""The JSON Schema of each setting, self-contained: what a form needs to edit it without knowing pydantic."""

import warnings
from typing import Any, cast

from pydantic.errors import PydanticInvalidForJsonSchema, PydanticUserError
from pydantic.json_schema import PydanticJsonSchemaWarning

from heinzel import ModuleSettings
from heinzel.kernel import toml_value

_PREFIX = "#/$defs/"


def setting_schemas(model: type[ModuleSettings]) -> dict[str, dict[str, Any]]:
    """Each field's JSON Schema, with every `$ref` resolved inline; empty when the model has no JSON Schema."""
    try:
        with warnings.catch_warnings():
            # A default JSON can't hold is left out of the schema; `default` on the setting still says what it is.
            warnings.simplefilter("ignore", PydanticJsonSchemaWarning)
            # By field name, not alias: a setting also read from GH_TOKEN is still `github_token` in its table.
            schema = model.model_json_schema(by_alias=False)
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


def reference_hints(schema: dict[str, Any] | None) -> str:
    """What the web app shows as controls, in words for a file or the docs: the choices and an example in TOML.

    Settings offers the choices of an `enum` and uses an example as a placeholder; a configuration file and
    CONFIGURATION.md say them instead, e.g. ` Choices: text, json. Example: "sonnet".`
    """
    if not schema:
        return ""
    branches = cast("list[dict[str, Any]]", [schema, *schema.get("anyOf", [])])
    enum = next((branch["enum"] for branch in branches if isinstance(branch.get("enum"), list)), None)
    examples = schema.get("examples")
    hints = ""
    if enum:
        hints += " Choices: " + ", ".join(str(choice) for choice in cast("list[object]", enum)) + "."
    if isinstance(examples, list) and examples:
        hints += f" Example: {toml_value(cast('list[object]', examples)[0])}."
    return hints
