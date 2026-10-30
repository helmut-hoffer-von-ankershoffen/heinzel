"""Host-bound private skill proposals for a durable personal model turn.

This internal adapter has no runner registration, publication, approval or execution capability. Its owning host
must derive TurnDeps from trusted authority, not a model or client. Draft bodies live only in the private skill store.
"""

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from heinzel import HeinzelError, SessionWriter
from heinzel.modules.heinzel._engine import TurnDeps
from heinzel.modules.heinzel._model_grants import ModelGrant, ModelGrants
from heinzel.modules.heinzel._session_crud import SessionCrud

_REQUEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z", re.ASCII)


class _DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=1, max_length=128)
    name: str
    requested_scope: Literal["personal", "team", "org", "site", "company"]
    scope_ref: str | None = None
    files: dict[str, str]


class _ShareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=1, max_length=128)
    draft_request_id: str
    draft_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def skill_tool_schemas(grant: ModelGrant) -> list[dict[str, JsonValue]]:
    """Describe only the inert private tools explicitly approved in this model grant."""
    schemas: dict[str, type[BaseModel]] = {"skill.propose_draft": _DraftRequest, "skill.propose_sharing": _ShareRequest}
    return [{"name": name, "schema": schemas[name].model_json_schema()} for name in grant.allowed_skill_tools]


class TurnSkillAdapter:
    """Only private skill operations, scoped by trusted turn identity and rechecked at each invocation."""

    def __init__(self, service: SessionCrud, deps: TurnDeps, grant: ModelGrant, writer: SessionWriter) -> None:
        self._service = service
        self._session = deps.session_id
        self._turn = deps.turn_id
        self._owner = deps.authority.principal_id
        self._grant = grant
        self._writer = writer
        if deps.authority.grant_revision != grant.revision:
            msg = "The skill tool grant does not match this turn"
            raise HeinzelError(msg)
        self._check_owner()

    def _check_owner(self) -> None:
        if self._service.repository.principal_id != self._owner:
            msg = "The skill tool is unavailable for this personal turn"
            raise HeinzelError(msg)
        if not self._service.repository.is_main_writer(self._session, self._writer):
            msg = "The skill tool requires its owning turn lock"
            raise HeinzelError(msg)
        if ModelGrants(self._service).approved(self._session) != self._grant:
            msg = "The skill tool grant changed"
            raise HeinzelError(msg)

    def _reference(self, name: str, request: str) -> str:
        if _REQUEST.fullmatch(request) is None:
            msg = "The skill request identity is invalid"
            raise HeinzelError(msg)
        value = f"{self._session}\0{self._turn}\0{name}\0{request}".encode()
        return "turn-" + hashlib.sha256(value).hexdigest()

    def dispatch(self, name: str, arguments: dict[str, JsonValue]) -> str:
        """Stage only supported private proposals; return no files or unsanitized errors to a model.

        Raises:
            HeinzelError: If the turn owner or private journal cannot be verified.
        """
        self._check_owner()
        if name not in self._grant.allowed_skill_tools:
            return "Tool unavailable under this grant."
        try:
            if name == "skill.propose_draft":
                request = _DraftRequest.model_validate(arguments)
                reference = self._reference(name, request.request_id)
                result = self._service.propose_skill_draft(
                    self._session, reference, request.name, request.requested_scope, request.scope_ref, request.files
                )
                response = {"request_id": result.request_id, "preview_sha256": result.preview_sha256, "status": "draft"}
            else:
                share = _ShareRequest.model_validate(arguments)
                reference = self._reference(name, share.request_id)
                proposal = self._service.propose_skill_sharing(
                    self._session, reference, share.draft_request_id, share.draft_sha256
                )
                response = {"request_id": proposal.request_id, "status": "awaiting_review", "authority": "unverified"}
        except Exception:  # noqa: BLE001 - validation/storage errors can contain private draft bytes
            return "Could not confirm the private skill proposal; retry the same request or check its content."
        self._check_owner()
        with self._service.repository.control_writer(self._session) as writer:
            writer.append(
                "interactive.skill-tool",
                turn=self._turn,
                key=f"skill-{reference}",
                data={"tool": name, "status": "completed"},
            )
        return json.dumps(response)
