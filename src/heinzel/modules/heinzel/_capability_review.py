"""Describe a selected read capability method for private human review; it does not invoke or authorize it."""

import hashlib
import inspect
import json
from dataclasses import dataclass
from typing import Any, get_args, get_type_hints

from pydantic import BaseModel, ConfigDict, create_model

from heinzel import HeinzelError, Kernel, Window
from heinzel.kernel import CAPABILITIES, capability_read_schema

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
        schema, _digest = capability_read_schema(capability, method)
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


def validate_capability_read_arguments(capability: str, method: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate exact fixed arguments against the actual read protocol, rejecting ignored nested fields.

    Raises:
        HeinzelError: If arguments are unsupported, incomplete, or would be silently changed.
        ValueError: Invalid argument representations are converted to a safe error.
    """
    spec = CAPABILITIES.get(capability)
    if spec is None or spec.methods.get(method) != "read":
        msg = "The selected capability method is not an available read"
        raise HeinzelError(msg)
    try:
        function = getattr(spec.protocol, method)
        signature = inspect.signature(function)
        hints = get_type_hints(function)
        fields: dict[str, Any] = {}
        for name, parameter in signature.parameters.items():
            if name != "self":
                fields[name] = (hints[name], ... if parameter.default is inspect.Parameter.empty else parameter.default)
        model = create_model("FixedCapabilityRead", __config__=ConfigDict(extra="forbid"), **fields)
        validated = model.model_validate(arguments).model_dump(mode="json", exclude_unset=True)

        def canonical(value: object) -> str:
            return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)

        if canonical(validated) != canonical(arguments):
            raise ValueError  # noqa: TRY301
    except Exception:  # noqa: BLE001 - input diagnostics may contain private arguments
        msg = "The connected read arguments do not match its capability schema"
        raise HeinzelError(msg) from None
    else:
        return validated


def rolling_window_parameter(capability: str, method: str) -> str:
    """Identify exactly one direct Window argument of a registered read protocol method.

    Raises:
        HeinzelError: If the method has no unique direct Window argument.
    """
    spec = CAPABILITIES.get(capability)
    if spec is None or spec.methods.get(method) != "read":
        msg = "The method is not an available read"
        raise HeinzelError(msg)
    try:
        function = getattr(spec.protocol, method)
        hints = get_type_hints(function)
        parameters = [name for name in inspect.signature(function).parameters if name != "self"]
        names = [name for name in parameters if hints.get(name) is Window]

        def contains_window(annotation: Any, seen: frozenset[type] = frozenset()) -> bool:  # noqa: ANN401
            if annotation is Window:
                return True
            if isinstance(annotation, type) and issubclass(annotation, BaseModel) and annotation not in seen:
                return any(
                    contains_window(field.annotation, seen | {annotation}) for field in annotation.model_fields.values()
                )
            return any(contains_window(child, seen) for child in get_args(annotation))

        if len(names) == 1 and not any(contains_window(hints.get(name)) for name in parameters if name != names[0]):
            return names[0]
    except Exception:  # noqa: BLE001 - no plugin diagnostics escape
        msg = "The read must have exactly one direct Window parameter"
        raise HeinzelError(msg) from None
    msg = "The read must have exactly one direct Window parameter"
    raise HeinzelError(msg)
