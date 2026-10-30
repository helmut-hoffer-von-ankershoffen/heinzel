"""Internal host-selected operation catalog; grants never authorize a write."""

import hashlib
import inspect
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from pydantic import ConfigDict, JsonValue, create_model
from pydantic_core import to_jsonable_python

from heinzel import CurrentUser, HeinzelError, Kernel, SessionWriter
from heinzel.kernel._exposure import Exposed, exposed, input_schema
from heinzel.modules.heinzel._model_grants import ModelGrant, ModelGrants
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._tool_argument_policy import ExactArgumentPolicy, Scalar

_MAX_SCHEMA = 16_000
_MAX_RESULT = 16_000
_DIGEST_LENGTH = 64


def _identity(item: Exposed) -> str:
    return f"{item.record.qualname}:{item.operation.name}"


def _schema(item: Exposed, implementation: str) -> tuple[dict[str, Any], str]:
    schema = input_schema(item)
    schema["additionalProperties"] = False

    def local_refs(value: object) -> bool:
        if isinstance(value, dict):
            entries = cast("dict[object, object]", value)
            return all(
                (key != "$ref" or (isinstance(child, str) and child.startswith("#/"))) and local_refs(child)
                for key, child in entries.items()
            )
        return all(local_refs(child) for child in cast("list[object]", value)) if isinstance(value, list) else True

    encoded = json.dumps(schema, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode()) > _MAX_SCHEMA or not local_refs(schema):
        msg = "This operation schema is not supported"
        raise HeinzelError(msg)
    origin = item.record.origin
    binding = json.dumps(
        {
            "identity": _identity(item),
            "summary": item.operation.summary,
            "implementation": implementation,
            "schema": schema,
            "distribution": origin.distribution,
            "version": origin.version,
            "builtin": origin.builtin,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return schema, hashlib.sha256(binding.encode()).hexdigest()


class OperationFailedError(HeinzelError):
    """The operation was invoked but did not produce a usable result."""


@dataclass(frozen=True, slots=True)
class ToolPreview:
    """Host-selected registered operation and the exact input schema to review in a grant."""

    identity: str
    schema: dict[str, Any]
    binding_sha256: str
    distribution: str
    version: str
    builtin: bool


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """Human-reviewable registration and fixed argument constraints, not an authority attestation."""

    identity: str
    description: str
    schema: dict[str, Any]
    binding_sha256: str
    distribution: str
    version: str
    builtin: bool
    fixed_arguments: dict[str, Scalar]
    policy_sha256: str
    implementation_sha256: str
    code_evidence: str
    scope_evidence: str
    executable: bool


class HostToolCatalog:
    """An owner-bound catalog requiring both a reviewed grant and independent host read policy.

    The host supplies its allowlist, never the model. Invoke only while the owning turn holds its main writer,
    so approval changes cannot interleave with an operation call. No operation marked write is ever invoked here.
    """

    def __init__(  # noqa: PLR0913 - dependencies and trusted policy are independently supplied
        self,
        kernel: Kernel,
        current: CurrentUser,
        crud: SessionCrud,
        session: str,
        host_reads: frozenset[str],
        *,
        verified_code: Callable[[Exposed], str] | None = None,
        argument_policies: Mapping[str, ExactArgumentPolicy] | None = None,
        code_evidence: str = "Implementation identity asserted by host verifier; independently unverified here.",
    ) -> None:
        self._kernel = kernel
        self._current = current
        self._crud = crud
        self._session = session
        self._owner = crud.repository.principal_id
        self._host_reads = frozenset(host_reads)
        self._verified_code = verified_code
        self._code_evidence = code_evidence
        self._argument_policies = dict(argument_policies or {})
        if kernel.resolve(CurrentUser) is not current or current.principal().id != self._owner:
            msg = "The operation catalog owner cannot be verified"
            raise HeinzelError(msg)
        crud.repository.control_read(session)

    def _candidate(self, name: str) -> Exposed | None:
        candidates = [item for item in exposed(self._kernel, "mcp") if _identity(item) == name]
        if len(candidates) != 1:
            return None
        item = candidates[0]
        if name not in self._host_reads or not item.operation.read_only or item.operation.private:
            return None
        return item

    def _find(self, name: str) -> Exposed | None:
        item = self._candidate(name)
        # The fixed argument policy does not verify provider identity or non-parameter ambient resources.
        return item if item is not None and not item.parameters else None

    def _implementation(self, item: Exposed) -> str:
        if self._verified_code is None:
            msg = "This operation has no verified implementation identity"
            raise HeinzelError(msg)
        try:
            identity = cast("object", self._verified_code(item))
        except Exception:  # noqa: BLE001 - verifier details may contain private state
            msg = "This operation has no verified implementation identity"
            raise HeinzelError(msg) from None
        if (
            not isinstance(identity, str)
            or len(identity) != _DIGEST_LENGTH
            or any(c not in "0123456789abcdef" for c in identity)
        ):
            msg = "This operation has no verified implementation identity"
            raise HeinzelError(msg)
        return identity

    def _binding(self, item: Exposed) -> tuple[dict[str, Any], str]:
        return _schema(item, self._implementation(item))

    def preview(self) -> list[ToolPreview]:
        """Show only independently host-allowed read operations, without running them."""
        self._check_owner()
        previews: list[ToolPreview] = []
        for name in sorted(self._host_reads):
            item = self._find(name)
            if item is not None:
                schema, digest = self._binding(item)
                origin = item.record.origin
                previews.append(ToolPreview(name, schema, digest, origin.distribution, origin.version, origin.builtin))
        return previews

    def review_descriptors(self) -> list[ToolDescriptor]:
        """Describe configured read constraints without asserting scope authority or running operations.

        Raises:
            HeinzelError: If a supplied policy or implementation identity is stale or incomplete.
        """
        self._check_owner()
        descriptors: list[ToolDescriptor] = []
        for name in sorted(self._host_reads):
            item = self._candidate(name)
            if item is None:
                continue
            schema, digest = self._binding(item)
            policy = self._argument_policies.get(name)
            if policy is None:
                if item.parameters:
                    continue
                policy = ExactArgumentPolicy(name, digest, ())
            declared = tuple(parameter.name for parameter in item.parameters)
            fixed = policy.apply(name, digest, declared, {})
            implementation = self._implementation(item)
            if _schema(item, implementation)[1] != digest:
                msg = "This operation changed during review"
                raise HeinzelError(msg)
            origin = item.record.origin
            descriptors.append(
                ToolDescriptor(
                    name,
                    item.operation.summary,
                    schema,
                    digest,
                    origin.distribution,
                    origin.version,
                    origin.builtin,
                    fixed,
                    policy.fingerprint(),
                    implementation,
                    self._code_evidence,
                    "Ambient account and resource authority not verified."
                    if declared
                    else "No model arguments; host read allowlist applies.",
                    not declared,
                )
            )
        return descriptors

    def review_descriptors_for(self, session: str) -> list[ToolDescriptor]:
        """Resolve descriptors for the exact authenticated personal session.

        Raises:
            HeinzelError: If the catalog and requested session are different or ownership is unavailable.
        """
        self._check_owner()
        if session != self._session:
            msg = "The review catalog belongs to a different session"
            raise HeinzelError(msg)
        self._crud.repository.control_read(session)
        return self.review_descriptors()

    def _check_owner(self) -> None:
        if self._crud.repository.principal_id != self._owner or self._current.principal().id != self._owner:
            msg = "The operation catalog belongs to a different owner"
            raise HeinzelError(msg)

    def approved_preview(self, session: str, grant: ModelGrant) -> list[ToolPreview]:
        """Return only exact approved schemas for this owner-bound session.

        Raises:
            HeinzelError: If the selected session, grant or any approved operation is stale.
        """
        self._check_owner()
        if session != self._session or ModelGrants(self._crud).approved(session) != grant:
            msg = "The operation catalog does not match the current grant"
            raise HeinzelError(msg)
        available = {item.identity: item for item in self.preview()}
        if any(
            name not in available or available[name].binding_sha256 != digest
            for name, digest in grant.allowed_tools.items()
        ):
            msg = "An approved operation is unavailable or has changed"
            raise HeinzelError(msg)
        selected = [available[name] for name in sorted(grant.allowed_tools)]
        if len(json.dumps([item.schema for item in selected]).encode()) > _MAX_SCHEMA:
            msg = "The approved operation catalog is too large"
            raise HeinzelError(msg)
        return selected

    def invoke(
        self, grant: ModelGrant, name: str, arguments: Mapping[str, JsonValue], *, turn_writer: SessionWriter
    ) -> str:
        """Execute a registered read only if the current approved grant and host policy both allow it.

        Raises:
            HeinzelError: If owner, grant, schema, operation or arguments do not match.
            OperationFailedError: If an invoked operation does not produce a usable result.
        """
        self._check_owner()
        if not self._crud.repository.is_main_writer(self._session, turn_writer):
            msg = "The operation requires its owning turn lock"
            raise HeinzelError(msg)
        if ModelGrants(self._crud).approved(self._session) != grant:
            msg = "The operation grant changed"
            raise HeinzelError(msg)
        item = self._find(name)
        if item is None or name not in grant.allowed_tools:
            msg = "The operation is unavailable"
            raise HeinzelError(msg)
        _, fingerprint = self._binding(item)
        if grant.allowed_tools[name] != fingerprint:
            msg = "The operation schema changed; review a new grant"
            raise HeinzelError(msg)
        policy = self._argument_policies.get(name) or ExactArgumentPolicy(name, fingerprint, ())
        try:
            constrained = policy.apply(
                name, fingerprint, tuple(parameter.name for parameter in item.parameters), dict(arguments)
            )
        except HeinzelError:
            msg = "Invalid operation arguments"
            raise HeinzelError(msg) from None
        try:
            fields: dict[str, Any] = {
                parameter.name: (
                    parameter.annotation,
                    ... if parameter.default is inspect.Parameter.empty else parameter.default,
                )
                for parameter in item.parameters
            }
            model = create_model(item.name, __config__=ConfigDict(extra="forbid", strict=True), **fields)
            validated = model.model_validate(constrained).model_dump()
        except Exception:  # noqa: BLE001 - schema diagnostics may contain private input
            msg = "Invalid operation arguments"
            raise HeinzelError(msg) from None
        self._check_owner()
        latest = self._find(name)
        if (
            ModelGrants(self._crud).approved(self._session) != grant
            or latest is None
            or latest != item
            or self._binding(latest)[1] != fingerprint
        ):
            msg = "The operation grant or registration changed"
            raise HeinzelError(msg)
        try:
            result = self._kernel.invoke(item.operation, **validated)
            text = json.dumps(to_jsonable_python(result, fallback=str), ensure_ascii=False)
            if len(text.encode()) > _MAX_RESULT:
                msg = "The operation result exceeds the safe limit"
                raise OperationFailedError(msg)  # noqa: TRY301 - an invoked operation failed to produce usable output
        except Exception:  # noqa: BLE001 - never expose operation diagnostics to the model
            msg = "The operation was invoked but did not produce a usable result"
            raise OperationFailedError(msg) from None
        return text
