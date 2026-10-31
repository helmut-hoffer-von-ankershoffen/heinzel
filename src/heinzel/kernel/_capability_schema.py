"""Public, read-only schemas for the kernel's declared capability protocol methods."""

import hashlib
import inspect
import json
from typing import Any, get_type_hints

from pydantic import ConfigDict, TypeAdapter, create_model

from heinzel.kernel._capabilities import CAPABILITIES
from heinzel.kernel._errors import HeinzelError

_MAX_SCHEMA_BYTES = 16_000


def capability_read_schema(capability: str, method: str) -> tuple[dict[str, Any], str]:
    """Return the actual read protocol input schema and its canonical identity, without granting access.

    Raises:
        HeinzelError: If the capability method is not a safely describable read.
        ValueError: Invalid protocol metadata is converted to a safe error.
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
            if name == "self":
                continue
            if parameter.kind not in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
                raise ValueError  # noqa: TRY301
            default = ... if parameter.default is inspect.Parameter.empty else parameter.default
            fields[name] = (hints[name], default)
        model = create_model("CapabilityRead", __config__=ConfigDict(extra="forbid"), **fields)
        schema = TypeAdapter(model).json_schema(mode="validation")
        encoded = json.dumps(schema, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > _MAX_SCHEMA_BYTES:
            raise ValueError  # noqa: TRY301
        digest = hashlib.sha256(b"hz-approval-arguments-v1\0" + encoded).hexdigest()
    except Exception:  # noqa: BLE001 - never expose protocol or schema diagnostics
        msg = "The selected capability read schema is unavailable"
        raise HeinzelError(msg) from None
    return schema, digest
