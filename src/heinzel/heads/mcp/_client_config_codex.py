"""Verify Codex's native config/read layer and origin evidence.

The trusted host requests config/read with includeLayers=true and the exact physical workspace. The raw result
is private and can include other config. Only the source/version facts for this registration leave this parser.
Protocol: https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/schema/json/v2/ConfigReadResponse.json
"""

import re
from dataclasses import dataclass, field
from typing import cast

from heinzel.heads.mcp._client_config import ClientBridgeBinding, ClientConfigContext, _load_json

_MAX_RESPONSE = 1024 * 1024
_VERSION = re.compile(r"sha256:[0-9a-f]{64}")
_KNOWN_SOURCES = {
    "packagedDefaults",
    "mdm",
    "system",
    "enterpriseManaged",
    "user",
    "project",
    "sessionFlags",
    "legacyManagedConfigTomlFromFile",
    "legacyManagedConfigTomlFromMdm",
}


@dataclass(frozen=True, slots=True)
class CodexConfigProvenance:
    """The standard user layer and whether it supplied the exact target entry."""

    layer_version: str = field(repr=False)
    selected: bool


@dataclass(frozen=True, slots=True)
class _UserLayer:
    entries: dict[str, object] = field(repr=False)
    metadata: dict[str, object] = field(repr=False)
    version: str = field(repr=False)


def codex_config_provenance(
    context: ClientConfigContext, binding: ClientBridgeBinding, raw_result: str | None
) -> CodexConfigProvenance | None:
    """Refuse alternate user profiles, competing definitions and missing or incomplete layer evidence."""
    if raw_result is None or len(raw_result) > _MAX_RESPONSE:
        return None
    try:
        payload = _load_json(raw_result)
    except (ArithmeticError, ValueError, RecursionError):
        return None
    result = _table(payload)
    if result is None:
        return None
    profile = _standard_layer(result.get("layers"), context, binding.name)
    effective = _mcp_table(result.get("config"))
    origins = _table(result.get("origins"))
    if profile is None or effective is None or origins is None:
        return None
    return _prove_target(context, binding, profile, effective, origins)


def _prove_target(
    context: ClientConfigContext,
    binding: ClientBridgeBinding,
    profile: _UserLayer,
    effective: dict[str, object],
    origins: dict[str, object],
) -> CodexConfigProvenance | None:
    name = binding.name
    prefix = f"mcp_servers.{name}"
    evidence = {key: origin for key, origin in origins.items() if key == prefix or key.startswith(f"{prefix}.")}
    if name not in profile.entries and name not in effective and not evidence:
        return CodexConfigProvenance(profile.version, selected=False)
    expected = binding.entry(context.client)
    if profile.entries.get(name) != expected or effective.get(name) != expected:
        return None
    expected_origin = {"name": profile.metadata, "version": profile.version}
    # The loader records the origin of each array element with its numeric segment.
    keys = ("command", "args.0", "args.1", "args.2")
    if evidence != {f"{prefix}.{key}": expected_origin for key in keys}:
        return None
    return CodexConfigProvenance(profile.version, selected=True)


def _standard_layer(value: object, context: ClientConfigContext, name: str) -> _UserLayer | None:
    if not isinstance(value, list):
        return None
    selected = None
    for value_layer in cast("list[object]", value):
        layer = _table(value_layer)
        if layer is None:
            return None
        metadata = _table(layer.get("name"))
        entries = _mcp_table(layer.get("config"))
        kind = None if metadata is None else metadata.get("type")
        if metadata is None or entries is None or not isinstance(kind, str) or kind not in _KNOWN_SOURCES:
            return None
        if kind == "user":
            version = layer.get("version")
            if (
                selected is not None
                or metadata.get("file") != str(context.path)
                or metadata.get("profile") is not None
                or layer.get("disabledReason") is not None
                or not isinstance(version, str)
                or not _VERSION.fullmatch(version)
            ):
                return None
            selected = _UserLayer(entries, metadata, version)
        elif name in entries:
            return None
    return selected


def _mcp_table(config: object) -> dict[str, object] | None:
    table = _table(config)
    return None if table is None else _table(table.get("mcp_servers", {}))


def _table(value: object) -> dict[str, object] | None:
    return cast("dict[str, object]", value) if isinstance(value, dict) else None
