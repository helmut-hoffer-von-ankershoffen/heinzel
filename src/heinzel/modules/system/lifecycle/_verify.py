"""Proof that a replacement server loaded the expected code and configuration: the models and the comparison.

The routes that serve this (and the streamed-body bound) stay in the web head; nothing here needs a web toolkit.
"""

import json
import re
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError
from pydantic.errors import PydanticInvalidForJsonSchema, PydanticUserError

from heinzel.kernel import Kernel, Settings

_ORIGIN_POLICY = ("hz", "ui_allowed_origins")
_FEATURE_FLAGS = ("hz", "features")
_SECRET = re.compile(
    r"(?:^|_)(?:token|password|passphrase|secret|api_key|credential|private_key)(?:$|_)", re.IGNORECASE
)


class Restart(BaseModel):
    """The retiring server's acknowledgement; a replacement instance must still be verified."""

    restarting: bool = Field(description="Always true: wait for a different /lifecycle instance.")
    message: str = Field(description="What happens, for the person waiting.")
    instance: str = Field(description="The retiring server instance; an answer from it cannot prove a restart.")


class Lifecycle(BaseModel):
    """Facts about code actually loaded by this UI server, independent of the installation on disk."""

    instance: str = Field(description="An opaque identifier generated for this server instance.")
    version: str = Field(description="The hz code version loaded by this running server.")
    restartable: bool = Field(description="Whether this server can restart itself using the existing socket handoff.")


class ExpectedSetting(BaseModel):
    """A value the person expects this server to have loaded, never returned as configuration data."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    section: str = Field(min_length=1, max_length=200, description="The setting's owning section.")
    key: str = Field(min_length=1, max_length=200, description="The setting name within its section.")
    value: JsonValue = Field(description="The exact JSON value that must have been loaded.")


class ExpectedLifecycle(BaseModel):
    """The exact process, code and selected settings whose loaded state must agree."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    instance: str = Field(min_length=1, max_length=128, description="The replacement instance being verified.")
    version: str = Field(min_length=1, max_length=128, description="The expected loaded code version.")
    settings: list[ExpectedSetting] = Field(max_length=100, description="Only the target settings being applied.")


class LifecycleVerified(BaseModel):
    """Whether this exact server loaded the requested code and configuration; no raw loaded values are exposed."""

    instance: str = Field(description="The instance that answered this verification.")
    version: str = Field(description="The code version loaded by the answering instance.")
    matches: bool = Field(description="Whether instance, version and every expected loaded setting agree.")
    mismatched: list[str] = Field(description="Names of unknown, duplicated or differently loaded settings.")


def _safe_schema(node: object, definitions: dict[str, object], seen: frozenset[str] = frozenset()) -> bool:
    """Fail closed for private fields, secrets, nested secrets and unresolved or recursive schema references."""
    if isinstance(node, list):
        return all(_safe_schema(child, definitions, seen) for child in cast("list[object]", node))
    if not isinstance(node, dict):
        return True
    table = cast("dict[str, object]", node)
    if table.get("x-private") or table.get("writeOnly") or table.get("format") == "password":
        return False
    if "$ref" in table:
        reference = table["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/$defs/") or reference in seen:
            return False
        name = reference.removeprefix("#/$defs/")
        if name not in definitions or not _safe_schema(definitions[name], definitions, seen | {reference}):
            return False
    properties = table.get("properties", {})
    return not (
        isinstance(properties, dict) and any(_SECRET.search(key) for key in cast("dict[str, object]", properties))
    ) and all(_safe_schema(child, definitions, seen) for key, child in table.items() if key != "$ref")


def loaded_settings(kernel: Kernel) -> dict[tuple[str, str], str]:
    """Capture only safe declared fields; a private or secret field never becomes an equality oracle."""
    loaded: dict[tuple[str, str], str] = {}
    for section, settings in kernel.settings_of():
        try:
            schema = type(settings).model_json_schema(by_alias=False)
        except (PydanticInvalidForJsonSchema, PydanticUserError):
            continue
        definitions = schema.get("$defs", {})
        properties = schema.get("properties", {})
        for key in type(settings).model_fields:
            if _SECRET.search(key) or key not in properties or not _safe_schema(properties[key], definitions):
                continue
            try:
                values = settings.model_dump(mode="json", include={key})
                if key in values:
                    value = values[key]
                    if (section, key) == _FEATURE_FLAGS and isinstance(settings, Settings):
                        value = sorted(settings.features)
                    loaded[section, key] = json.dumps(value, sort_keys=True, allow_nan=False)
            except ValueError:
                continue  # A plugin's non-JSON setting cannot be proven by a JSON expectation.
    return loaded


def _expected_value(key: tuple[str, str], value: JsonValue) -> str:
    if key == _FEATURE_FLAGS and isinstance(value, list) and all(isinstance(flag, str) for flag in value):
        return json.dumps(sorted(flag for flag in value if isinstance(flag, str)), allow_nan=False)
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _expected_origins(value: JsonValue, startup: Settings) -> frozenset[str] | None:
    """Reuse the core origin grammar for arrays without letting process environment replace an expectation."""
    if not isinstance(value, list) or not all(isinstance(origin, str) for origin in value):
        return None
    try:
        # Every other field is explicitly supplied from startup, so unrelated runtime env cannot influence proof.
        return Settings.model_validate(startup.model_dump() | {"ui_allowed_origins": value}).ui_allowed_origins
    except ValidationError:
        return None  # Do not reflect invalid guesses or their values in the verification response.


def mismatches(
    expected: ExpectedLifecycle,
    loaded: dict[tuple[str, str], str],
    startup: Settings,
    origin_policy: frozenset[str],
) -> list[str]:
    """Names of the expected settings that are duplicated or differ from what this process loaded at startup."""
    seen: set[tuple[str, str]] = set()
    found: list[str] = []
    for setting in expected.settings:
        key = setting.section, setting.key
        agrees = (
            _expected_origins(setting.value, startup) == origin_policy
            if key == _ORIGIN_POLICY
            else loaded.get(key) == _expected_value(key, setting.value)
        )
        if key in seen or not agrees:
            found.append(f"{setting.section}.{setting.key}")
        seen.add(key)
    return found


def requires_owner_session(expected: ExpectedLifecycle) -> bool:
    """Whether the expectation names the origin policy, which only the owning web session may prove."""
    return any((setting.section, setting.key) == _ORIGIN_POLICY for setting in expected.settings)
