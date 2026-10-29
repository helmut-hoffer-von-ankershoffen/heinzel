"""Internal host-selected operation catalog; grants never authorize a write."""

import hashlib
import inspect
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ConfigDict, JsonValue, create_model
from pydantic_core import to_jsonable_python

from heinzel import CurrentUser, HeinzelError, Kernel
from heinzel.kernel._exposure import Exposed, exposed, input_schema
from heinzel.modules.heinzel._model_grants import ModelGrant, ModelGrants
from heinzel.modules.heinzel._session_crud import SessionCrud

_MAX_SCHEMA = 16_000
_MAX_RESULT = 16_000


def _identity(item: Exposed) -> str:
    return f"{item.record.qualname}:{item.operation.name}"


def _schema(item: Exposed) -> tuple[dict[str, Any], str]:
    schema = input_schema(item)
    schema["additionalProperties"] = False

    def local_refs(value: object) -> bool:
        if isinstance(value, dict):
            return all(
                (key != "$ref" or (isinstance(child, str) and child.startswith("#/"))) and local_refs(child)
                for key, child in value.items()
            )
        return all(local_refs(child) for child in value) if isinstance(value, list) else True

    encoded = json.dumps(schema, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode()) > _MAX_SCHEMA or not local_refs(schema):
        msg = "This operation schema is not supported"
        raise HeinzelError(msg)
    origin = item.record.origin
    binding = json.dumps(
        {
            "identity": _identity(item),
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


@dataclass(frozen=True, slots=True)
class ToolPreview:
    """Host-selected registered operation and the exact input schema to review in a grant."""

    identity: str
    schema: dict[str, Any]
    binding_sha256: str
    distribution: str
    version: str
    builtin: bool


class HostToolCatalog:
    """An owner-bound catalog requiring both a reviewed grant and independent host read policy.

    The host supplies its allowlist, never the model. Invoke only while the owning turn holds its main writer,
    so approval changes cannot interleave with an operation call. No operation marked write is ever invoked here.
    """

    def __init__(
        self, kernel: Kernel, current: CurrentUser, crud: SessionCrud, session: str, host_reads: frozenset[str]
    ) -> None:
        self._kernel = kernel
        self._current = current
        self._crud = crud
        self._session = session
        self._owner = crud.repository.principal_id
        self._host_reads = frozenset(host_reads)
        if kernel.resolve(CurrentUser) is not current or current.principal().id != self._owner:
            msg = "The operation catalog owner cannot be verified"
            raise HeinzelError(msg)
        crud.repository.control_read(session)

    def _find(self, name: str) -> Exposed | None:
        candidates = [item for item in exposed(self._kernel, "mcp") if _identity(item) == name]
        if len(candidates) != 1:
            return None
        item = candidates[0]
        if name not in self._host_reads or not item.operation.read_only or item.operation.private:
            return None
        return item

    def preview(self) -> list[ToolPreview]:
        """Show only independently host-allowed read operations, without running them."""
        self._check_owner()
        previews = []
        for name in sorted(self._host_reads):
            item = self._find(name)
            if item is not None:
                schema, digest = _schema(item)
                origin = item.record.origin
                previews.append(ToolPreview(name, schema, digest, origin.distribution, origin.version, origin.builtin))
        return previews

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

    def invoke(self, grant: ModelGrant, name: str, arguments: Mapping[str, JsonValue]) -> str:
        """Execute a registered read only if the current approved grant and host policy both allow it.

        Raises:
            HeinzelError: If owner, grant, schema, operation or arguments do not match.
        """
        self._check_owner()
        if ModelGrants(self._crud).approved(self._session) != grant:
            msg = "The operation grant changed"
            raise HeinzelError(msg)
        item = self._find(name)
        if item is None or name not in grant.allowed_tools:
            msg = "The operation is unavailable"
            raise HeinzelError(msg)
        _, fingerprint = _schema(item)
        if grant.allowed_tools[name] != fingerprint:
            msg = "The operation schema changed; review a new grant"
            raise HeinzelError(msg)
        try:
            fields: dict[str, Any] = {
                parameter.name: (
                    parameter.annotation,
                    ... if parameter.default is inspect.Parameter.empty else parameter.default,
                )
                for parameter in item.parameters
            }
            model = create_model(item.name, __config__=ConfigDict(extra="forbid", strict=True), **fields)
            validated = model.model_validate(dict(arguments)).model_dump()
        except Exception:  # noqa: BLE001 - schema diagnostics may contain private input
            msg = "Invalid operation arguments"
            raise HeinzelError(msg) from None
        self._check_owner()
        latest = self._find(name)
        if (
            ModelGrants(self._crud).approved(self._session) != grant
            or latest is None
            or latest != item
            or _schema(latest)[1] != fingerprint
        ):
            msg = "The operation grant or registration changed"
            raise HeinzelError(msg)
        try:
            result = self._kernel.invoke(item.operation, **validated)
        except Exception:  # noqa: BLE001 - never expose operation diagnostics to the model
            msg = "The operation could not be completed"
            raise HeinzelError(msg) from None
        text = json.dumps(to_jsonable_python(result, fallback=str), ensure_ascii=False)
        if len(text.encode()) > _MAX_RESULT:
            msg = "The operation result exceeds the safe limit"
            raise HeinzelError(msg)
        return text
