"""Host-bound private skill proposals for a durable personal model turn.

This internal adapter has no runner registration, publication, approval or execution capability. Its owning host
must derive TurnDeps from trusted authority, not a model or client. Draft bodies live only in the private skill store.
"""

import hashlib
import json
import re
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from heinzel import HeinzelError
from heinzel.modules.heinzel._engine import TurnDeps
from heinzel.modules.heinzel._session_protocol import SessionRepository

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


class SkillDraftService(Protocol):
    """Existing private owner-bound skill service; validation and immutable draft bodies remain its responsibility."""

    repository: SessionRepository

    def propose_skill_draft(  # noqa: PLR0913, PLR0917 - existing service signature
        self,
        session_id: str,
        request_id: str,
        name: str,
        requested_scope: str,
        scope_ref: str | None,
        files: dict[str, str],
    ) -> Any:  # noqa: ANN401 - real service returns its private typed draft
        """Stage one private draft, with no sharing or execution."""
        ...

    def propose_skill_sharing(self, session_id: str, request_id: str, draft_request_id: str, draft_sha256: str) -> Any:  # noqa: ANN401 - real service returns its private typed proposal
        """Stage one unverified sharing review, with no publication or approval."""
        ...


class TurnSkillAdapter:
    """Only private skill operations, scoped by trusted turn identity and rechecked at each invocation."""

    def __init__(self, service: SkillDraftService, deps: TurnDeps) -> None:
        self._service = service
        self._session = deps.session_id
        self._turn = deps.turn_id
        self._owner = deps.authority.principal_id
        self._check_owner()

    def _check_owner(self) -> None:
        if self._service.repository.principal_id != self._owner:
            msg = "The skill tool is unavailable for this personal turn"
            raise HeinzelError(msg)
        self._service.repository.control_read(self._session)

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
        if name not in {"skill.propose_draft", "skill.propose_sharing"}:
            return "Tool unavailable."
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
                result = self._service.propose_skill_sharing(
                    self._session, reference, share.draft_request_id, share.draft_sha256
                )
                response = {"request_id": result.request_id, "status": "awaiting_review", "authority": "unverified"}
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
