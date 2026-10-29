"""Owner-scoped model grant proposals reviewed and issued through the trusted approval seam.

This internal service is not a head operation. Neither a model nor an API client can call its proof issuer.
"""

import json
import re
import secrets
from datetime import timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator

from heinzel import HeinzelError, SessionEvent, SessionWriter
from heinzel.kernel._approval import (
    ApprovalEvidence,
    ApprovalIntent,
    ApprovalRecord,
    approval_proof_hash,
    arguments_hash,
    consume_approval,
)
from heinzel.modules.heinzel._configured_api_binding import ConfiguredApiSnapshot
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import RequestId, SessionId

_REQUEST = TypeAdapter(RequestId)
_SESSION = TypeAdapter(SessionId)


class ModelGrant(BaseModel):
    """Exact model and limits awaiting review; the model cannot change these values during a turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: RequestId
    revision: int = Field(ge=1)
    binding: ConfiguredApiSnapshot | None = None
    access: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=128)
    requests: int = Field(ge=1, le=100)
    total_tokens: int = Field(ge=1, le=100_000_000)
    output_tokens: int = Field(ge=1, le=32_768)
    timeout_seconds: float = Field(gt=0, le=7200, allow_inf_nan=False)
    message_cap_usd: Decimal = Field(gt=0, allow_inf_nan=False)
    session_cap_usd: Decimal = Field(gt=0, allow_inf_nan=False)
    owner_cap_usd: Decimal = Field(gt=0, allow_inf_nan=False)
    allowed_tools: dict[str, str] = Field(default_factory=dict, max_length=32)
    allowed_skill_tools: tuple[Literal["skill.propose_draft", "skill.propose_sharing"], ...] = Field(
        default=(), max_length=2
    )

    @model_validator(mode="after")
    def valid_tools(self) -> "ModelGrant":
        """Bind approved operation IDs to fingerprints of schema and registered distribution revision.

        Raises:
            ValueError: If a tool identity or fingerprint is malformed.
        """
        if len(set(self.allowed_skill_tools)) != len(self.allowed_skill_tools):
            msg = "The model grant contains a duplicate skill tool"
            raise ValueError(msg)
        if any(
            re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}:[a-z][a-z0-9-]{0,127}", name) is None
            or re.fullmatch(r"[a-f0-9]{64}", digest) is None
            for name, digest in self.allowed_tools.items()
        ):
            msg = "The model grant contains an invalid operation or schema fingerprint"
            raise ValueError(msg)
        return self


class ModelGrants:
    """Record proposed and explicitly approved model bindings in the owning conversation."""

    def __init__(self, crud: SessionCrud) -> None:
        self.crud = crud

    def _body(self, session: str, event: SessionEvent) -> ModelGrant:
        return ModelGrant.model_validate(self.crud._body(session, event))  # noqa: SLF001 - hash-verified private body

    def _latest(self, session: str, events: list[SessionEvent]) -> ModelGrant | None:
        event = next((event for event in reversed(events) if event.kind == "model.grant-approved"), None)
        if event is None:
            return None
        grant = self._body(session, event)
        matching = [
            item
            for item in events
            if item.kind == "approval.issued" and item.key == event.data.get("proof_hash") and item.seq < event.seq
        ]
        if len(matching) != 1:
            msg = "The saved model grant lacks its trusted approval evidence"
            raise HeinzelError(msg)
        try:
            record = ApprovalRecord.model_validate_json(json.dumps(matching[0].data))
        except ValueError:
            msg = "The saved model grant has invalid approval evidence"
            raise HeinzelError(msg) from None
        proposals = [
            item
            for item in events
            if item.kind == "model.grant-proposed" and item.key == grant.request_id and item.seq < matching[0].seq
        ]
        if len(proposals) != 1 or self._body(session, proposals[0]) != grant:
            msg = "The saved model grant lacks its original proposal"
            raise HeinzelError(msg)
        if (
            sum(item.data.get("consumed_nonce") == record.nonce for item in events) != 1
            or record.intent != self._intent(session, grant)
            or record.proof_hash != matching[0].key
            or record.nonce != event.data.get("consumed_nonce")
            or record.intent.model_dump(mode="json") != event.data.get("intent")
            or record.provenance != event.data.get("provenance")
            or any(
                item.kind == "approval.revoked" and item.key == record.nonce and item.seq > event.seq for item in events
            )
        ):
            msg = "The saved model grant has invalid or revoked approval evidence"
            raise HeinzelError(msg)
        return grant

    def propose(self, session: str, grant: ModelGrant) -> ModelGrant:
        """Persist an inert proposal with a stable request id and a revision against current approved authority.

        Raises:
            HeinzelError: If the request conflicts or the proposed revision is stale.
        """
        _SESSION.validate_python(session)
        with self.crud.repository.control_writer(session) as writer:
            events = self.crud.repository.control_read(session).events
            prior = next(
                (event for event in events if event.kind == "model.grant-proposed" and event.key == grant.request_id),
                None,
            )
            if prior:
                if self._body(session, prior) != grant:
                    msg = "This model-grant request identifies a different proposal"
                    raise HeinzelError(msg)
                return grant
            current = self._latest(session, events)
            if grant.revision != (current.revision + 1 if current else 1):
                msg = "The model grant changed; reload before proposing a revision"
                raise HeinzelError(msg)
            body = immutable_body(writer, "model-grant", grant.model_dump(mode="json"), control=True)
            writer.append("model.grant-proposed", key=grant.request_id, data=body.model_dump(mode="json"))
        return grant

    def _proposal(self, session: str, request_id: str, events: list[SessionEvent]) -> ModelGrant:
        event = next(
            (event for event in events if event.kind == "model.grant-proposed" and event.key == request_id), None
        )
        if event is None:
            msg = "No such model-grant proposal"
            raise HeinzelError(msg)
        grant = self._body(session, event)
        current = self._latest(session, events)
        if grant.revision != (current.revision + 1 if current else 1):
            msg = "The model grant changed; reload before reviewing it"
            raise HeinzelError(msg)
        return grant

    def _intent(self, session: str, grant: ModelGrant) -> ApprovalIntent:
        return ApprovalIntent(
            owner=self.crud.repository.principal_id,
            audience="personal-model-grant",
            session=session,
            action="approve-model-grant",
            resource=grant.model,
            arguments_hash=arguments_hash(
                grant.model_dump(
                    mode="json",
                    exclude=(
                        ({"allowed_tools"} if not grant.allowed_tools else set())
                        | ({"allowed_skill_tools"} if not grant.allowed_skill_tools else set())
                        | ({"binding"} if grant.binding is None else set())
                    ),
                )
            ),
            contract_revision=0,
            grant_revision=grant.revision,
            policy_revision=0,
        )

    def review(self, session: str, request_id: str) -> ModelGrant:
        """Return the exact current inert proposal for the trusted human review surface."""
        _SESSION.validate_python(session)
        _REQUEST.validate_python(request_id)
        return self._proposal(session, request_id, self.crud.repository.control_read(session).events)

    def issue(self, session: str, request_id: str, provenance: str) -> str:
        """Issue a short-lived exact-intent proof only from the existing trusted confirmation service."""
        _SESSION.validate_python(session)
        _REQUEST.validate_python(request_id)
        proof = secrets.token_urlsafe(32)
        digest = approval_proof_hash(proof)
        now = self.crud.clock()
        with self.crud.repository.control_writer(session) as writer:
            grant = self._proposal(session, request_id, self.crud.repository.control_read(session).events)
            record = ApprovalRecord(
                intent=self._intent(session, grant),
                proof_hash=digest,
                nonce=secrets.token_hex(32),
                provenance=provenance[:128],
                decision="approve",
                issued_at=now,
                expires_at=now + timedelta(minutes=5),
            )
            writer.append("approval.issued", key=digest, data=record.model_dump(mode="json"))
        return proof

    def approve(self, session: str, request_id: str, proof: str) -> ModelGrant:
        """Consume an issued proof against current owner and revision; replay only returns saved authority.

        Raises:
            HeinzelError: If the proposal or proof is invalid.
        """
        _SESSION.validate_python(session)
        _REQUEST.validate_python(request_id)
        events = self.crud.repository.control_read(session).events
        event = next(
            (event for event in events if event.kind == "model.grant-proposed" and event.key == request_id), None
        )
        if event is None:
            msg = "No such model-grant proposal"
            raise HeinzelError(msg)
        grant = self._body(session, event)
        intent = self._intent(session, grant)

        def current(events: list[SessionEvent]) -> ApprovalIntent:
            return self._intent(session, self._proposal(session, request_id, events))

        def effect(
            writer: SessionWriter, _events: list[SessionEvent], _evidence: ApprovalEvidence
        ) -> dict[str, JsonValue]:
            body = immutable_body(writer, "approved-model", grant.model_dump(mode="json"), control=True)
            return body.model_dump(mode="json")

        with self.crud.repository.main_writer(session):
            consume_approval(
                self.crud.repository,
                session,
                proof,
                intent,
                current,
                self.crud.clock,
                event_kind="model.grant-approved",
                effect_data=effect,
            )
        return grant

    def applied(self, session: str, request_id: str) -> bool:
        """Reconcile an exact grant decision without issuing proof or applying it again."""
        _SESSION.validate_python(session)
        _REQUEST.validate_python(request_id)
        events = self.crud.repository.control_read(session).events
        for index, event in enumerate(events):
            if event.kind == "model.grant-approved":
                grant = self._latest(session, events[: index + 1])
                if grant is not None and grant.request_id == request_id:
                    return True
        return False

    def approved(self, session: str) -> ModelGrant:
        """Read the current owner-scoped approved binding; unapproved proposals never authorize a turn.

        Raises:
            HeinzelError: If the owner has not approved a binding.
        """
        current = self._latest(session, self.crud.repository.control_read(session).events)
        if current is None:
            msg = "This session has no approved model grant"
            raise HeinzelError(msg)
        return current
