"""Internal, explicit exact-value constraints for host-approved registered read operations.

This pure primitive neither grants a tool nor verifies a caller, revision, schema, or operation safety. The host
must bind the fingerprint to the actual registered operation and run this check on every call under the current grant.
"""

import hashlib
import json
import re
from dataclasses import dataclass

from heinzel import HeinzelError

type Scalar = str | int | bool | None
_MAX_FIELDS = 32
_MAX_OPERATION_LENGTH = 256
_PAIR_SIZE = 2
_MAX_VALUE_BYTES = 4096
_MAX_INT = 2**53 - 1
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")


def _scalar(value: object) -> bool:
    return (
        value is None
        or type(value) is bool
        or (type(value) is int and -_MAX_INT <= value <= _MAX_INT)
        or (type(value) is str and len(value.encode("utf-8")) <= _MAX_VALUE_BYTES)
    )


@dataclass(frozen=True, slots=True)
class ExactArgumentPolicy:
    """All declared parameters must have explicit host-reviewed scalar values, including optional defaults.

    A model may omit fixed arguments; the host supplies their approved values. No additional model argument is
    accepted. Hosts must not use this for an operation with ambient account/resource selection outside parameters.
    """

    operation: str
    binding_sha256: str
    values: tuple[tuple[str, Scalar], ...]

    def __post_init__(self) -> None:
        """Reject malformed or mutable policy values rather than coercing them.

        Raises:
            HeinzelError: If the policy is not an immutable bounded exact-value policy.
        """
        denied = "Invalid exact argument policy"
        if (
            type(self.operation) is not str
            or not self.operation
            or len(self.operation) > _MAX_OPERATION_LENGTH
            or type(self.binding_sha256) is not str
            or not _DIGEST.fullmatch(self.binding_sha256)
            or type(self.values) is not tuple
            or len(self.values) > _MAX_FIELDS
        ):
            raise HeinzelError(denied)
        names: set[str] = set()
        for entry in self.values:
            if type(entry) is not tuple or len(entry) != _PAIR_SIZE:
                raise HeinzelError(denied)
            name, value = entry
            if type(name) is not str or not _NAME.fullmatch(name) or name in names:
                raise HeinzelError(denied)
            try:
                valid = _scalar(value)
            except UnicodeEncodeError:
                valid = False
            if not valid:
                raise HeinzelError(denied)
            names.add(name)

    def fingerprint(self) -> str:
        """Stable digest of operation binding and exact approved values, for an outer reviewed grant."""
        data = {"operation": self.operation, "binding_sha256": self.binding_sha256, "values": sorted(self.values)}
        return hashlib.sha256(json.dumps(data, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()

    def apply(
        self, operation: str, binding_sha256: str, declared: tuple[str, ...], arguments: dict[str, object]
    ) -> dict[str, Scalar]:
        """Return only fixed values after checking every declared and requested parameter.

        Raises:
            HeinzelError: If the binding, registered parameter names, or arguments differ from the policy.
        """
        denied = "The tool argument policy does not allow this call"
        fixed = dict(self.values)
        if (
            operation != self.operation
            or binding_sha256 != self.binding_sha256
            or type(declared) is not tuple
            or len(declared) != len(fixed)
            or any(type(name) is not str or name not in fixed for name in declared)
            or len(set(declared)) != len(declared)
            or type(arguments) is not dict
            or any(type(name) is not str or name not in fixed for name in arguments)
        ):
            raise HeinzelError(denied)
        for name, value in arguments.items():
            expected = fixed[name]
            if type(value) is not type(expected) or value != expected:
                raise HeinzelError(denied)
        return fixed
