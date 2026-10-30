"""Describe a selected read capability method for private human review; it does not invoke or authorize it."""

import hashlib
import inspect
import json
from dataclasses import dataclass
from typing import Any, get_type_hints

from pydantic import ConfigDict, TypeAdapter, create_model

from heinzel import HeinzelError, Kernel
from heinzel.kernel import CAPABILITIES

_MAX_SCHEMA_BYTES = 16_000


@dataclass(frozen=True, slots=True)
class CapabilityReadDescriptor:
    """Registration metadata and protocol schema; hashes are not code or publisher attestations."""

    kind: str
    capability: str
    method: str
    adapter: str
    owner_module: str
    distribution: str
    version: str
    factory: str
    schema: dict[str, Any]
    binding_sha256: str


def describe_capability_read(kernel: Kernel, capability: str, method: str) -> CapabilityReadDescriptor:
    """Describe the selected registered provider and the kernel protocol's exact read-method schema.

    The descriptor is not executable. Human approval, data/account binding, implementation continuity and current
    configuration must be separately checked by the caller before any provider is invoked.

    Raises:
        HeinzelError: If the capability, selected provider or read method cannot be described safely.
        ValueError: Invalid schema metadata is converted to a safe error.
    """
    spec = CAPABILITIES.get(capability)
    if spec is None or spec.methods.get(method) != "read":
        msg = "The selected capability method is not an available read"
        raise HeinzelError(msg)
    record, provider = kernel.selected_capability_provider(capability)
    function = getattr(spec.protocol, method, None)
    try:
        if function is None:
            raise ValueError  # noqa: TRY301
        signature = inspect.signature(function)
        hints = get_type_hints(function)
        fields: dict[str, Any] = {}
        for name, parameter in signature.parameters.items():
            if name == "self":
                continue
            if parameter.kind not in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
                raise ValueError  # noqa: TRY301
            if name not in hints:
                raise ValueError  # noqa: TRY301
            default = ... if parameter.default is inspect.Parameter.empty else parameter.default
            fields[name] = (hints[name], default)
        model = create_model("CapabilityRead", __config__=ConfigDict(extra="forbid"), **fields)
        schema = TypeAdapter(model).json_schema(mode="validation")
        factory = f"{provider.factory.__module__}.{provider.factory.__qualname__}"
        binding = {
            "kind": "capability-method",
            "capability": capability,
            "method": method,
            "mode": "read",
            "adapter": provider.adapter,
            "module": record.qualname,
            "distribution": record.origin.distribution,
            "version": record.origin.version,
            "factory": factory,
            "schema": schema,
        }
        encoded = json.dumps(binding, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
        if len(encoded) > _MAX_SCHEMA_BYTES:
            raise ValueError  # noqa: TRY301
    except Exception:  # noqa: BLE001 - never expose provider or schema diagnostics
        msg = "The selected capability method cannot be safely described"
        raise HeinzelError(msg) from None
    return CapabilityReadDescriptor(
        "capability-method",
        capability,
        method,
        provider.adapter,
        record.qualname,
        record.origin.distribution,
        record.origin.version,
        factory,
        schema,
        hashlib.sha256(encoded).hexdigest(),
    )
