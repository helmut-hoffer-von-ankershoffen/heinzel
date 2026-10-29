"""Owner-bound, inert private proposal tools for an explicitly requested personal chat turn.

No tool approves, executes, publishes, grants access, or starts a schedule. Artifact association accepts only a
canonical link present in the authenticated user's current message and never contacts the provider.
"""

import hashlib
import json
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from heinzel import HeinzelError, SessionWriter
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import HeinzelAppearance


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Empty(_Request):
    pass


class _Agent(_Request):
    expected_revision: int = Field(ge=0)
    contract: str = Field(min_length=1, max_length=4000)
    timeout_seconds: int = Field(ge=1, le=300)


class _Profile(_Request):
    expected_revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=100)
    appearance: HeinzelAppearance


class _Skill(_Request):
    name: str
    requested_scope: Literal["personal", "team", "org", "site", "company"]
    scope_ref: str | None = None
    files: dict[str, str]


class _Artifact(_Request):
    kind: Literal["document", "spreadsheet", "presentation"]
    ref: str


_SCHEMAS: dict[str, type[_Request]] = {
    "agent:current": _Empty,
    "agent:propose": _Agent,
    "profile:current": _Empty,
    "profile:propose": _Profile,
    "skill:propose-draft": _Skill,
    "artifact:associate": _Artifact,
}


def user_proposal_schemas() -> list[dict[str, JsonValue]]:
    """Only bounded host-controlled private proposal operations; no resource access or execution."""
    return [{"name": name, "schema": schema.model_json_schema()} for name, schema in _SCHEMAS.items()]


class UserProposalTools:
    """Bind proposal tools to the real current user, session, accepted turn and locked writer."""

    def __init__(  # noqa: PLR0913, PLR0917 - explicit session and origin fencing
        self,
        crud: SessionCrud,
        session: str,
        turn: str,
        owner: str,
        writer: SessionWriter,
        user_text: str,
        current_policy: Callable[[], bool],
    ) -> None:
        self._crud, self._session, self._turn, self._owner = crud, session, turn, owner
        self._writer, self._user_text, self._current_policy = writer, user_text, current_policy

    def _check(self) -> None:
        if (
            self._crud.repository.principal_id != self._owner
            or not self._crud.repository.is_main_writer(self._session, self._writer)
            or not self._current_policy()
        ):
            msg = "This private proposal tool is unavailable"
            raise HeinzelError(msg)

    def dispatch(self, name: str, arguments: dict[str, JsonValue], call: int) -> str:
        """Return a small reference or a redacted failure, never a draft body or approval.

        Raises:
            HeinzelError: If the authenticated turn authority has changed.
        """
        self._check()
        if name not in _SCHEMAS:
            msg = "This private proposal tool is unavailable"
            raise HeinzelError(msg)
        request_id = "turn-" + hashlib.sha256(f"{self._session}\0{self._turn}\0{call}\0{name}".encode()).hexdigest()
        try:
            if name == "agent:current":
                _Empty.model_validate(arguments)
                value = self._crud.latest_scheduled_agent_proposal(self._session)
                result = value.model_dump(mode="json") if value else None
            elif name == "agent:propose":
                agent = _Agent.model_validate(arguments)
                if agent.expected_revision == 0:
                    proposal = self._crud.propose_scheduled_agent(
                        self._session, request_id, agent.contract, None, agent.timeout_seconds
                    )
                else:
                    proposal = self._crud.amend_scheduled_agent(
                        self._session,
                        request_id,
                        agent.expected_revision,
                        agent.contract,
                        None,
                        agent.timeout_seconds,
                    )
                result = {
                    "request_id": proposal.request_id,
                    "revision": proposal.revision,
                    "preview_sha256": proposal.preview_sha256,
                    "status": "proposed",
                }
            elif name == "profile:current":
                _Empty.model_validate(arguments)
                snapshot = self._crud.snapshot(self._session)
                result = {
                    "name": snapshot.name,
                    "appearance": snapshot.appearance.model_dump(mode="json"),
                    "revision": snapshot.profile_revision,
                }
            elif name == "profile:propose":
                profile = _Profile.model_validate(arguments)
                profile_proposal = self._crud.propose_profile(
                    self._session, request_id, profile.expected_revision, profile.name, profile.appearance
                )
                result = {
                    "request_id": profile_proposal.request_id,
                    "preview_sha256": profile_proposal.preview_sha256,
                    "status": "proposed",
                }
            elif name == "skill:propose-draft":
                skill = _Skill.model_validate(arguments)
                draft = self._crud.propose_skill_draft(
                    self._session, request_id, skill.name, skill.requested_scope, skill.scope_ref, skill.files
                )
                result = {"request_id": draft.request_id, "preview_sha256": draft.preview_sha256, "status": "draft"}
            else:
                artifact = _Artifact.model_validate(arguments)
                supplied = any(token.strip("\"'()[]<>,;.") == artifact.ref for token in self._user_text.split())
                if not supplied:
                    msg = "The current user message did not supply this artifact link"
                    raise HeinzelError(msg)  # noqa: TRY301 - errors are redacted to the model
                link = self._crud.associate_artifact(self._session, request_id, artifact.kind, artifact.ref)
                result = {"request_id": link.request_id, "status": "associated", "access": "unverified"}
        except Exception:  # noqa: BLE001 - validation and private bodies never leave via error text
            msg = "The private proposal could not be recorded; check the request and current revision"
            raise HeinzelError(msg) from None
        return json.dumps(result, ensure_ascii=True, separators=(",", ":"))
