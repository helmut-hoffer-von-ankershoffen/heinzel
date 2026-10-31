"""Exact owner-private contract acceptance; acceptance grants no execution or access."""

import json
import secrets
from datetime import timedelta

from pydantic import BaseModel, Field, JsonValue

from heinzel import HeinzelError, SessionEvent, SessionWriter
from heinzel.kernel._approval import (
    ApprovalEvidence,
    ApprovalIntent,
    ApprovalRecord,
    approval_proof_hash,
    arguments_hash,
    consume_approval,
)
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import ScheduledAgentProposal


class ContractReviewView(BaseModel):
    """An owner-private acceptance record, not an execution or access grant."""

    proposal: ScheduledAgentProposal = Field(description="Exact proposal revision and preview hash.")
    accepted: bool = Field(description="Whether the trusted confirmation boundary recorded this exact acceptance.")


class ContractReview:
    """The issuer is internal; only trusted confirmation may issue an exact decision."""

    def __init__(self, crud: SessionCrud) -> None:
        self.crud = crud

    def current(self, session: str, request: str) -> ScheduledAgentProposal:
        """Return only the current exact proposal.

        Raises:
            HeinzelError: If the proposal is superseded.
        """
        proposal = self.crud.scheduled_agent_proposal(session, request)
        latest = self.crud.latest_scheduled_agent_proposal(session)
        if proposal != latest:
            msg = "The contract proposal changed; review the latest revision"
            raise HeinzelError(msg)
        return proposal

    def intent(self, proposal: ScheduledAgentProposal) -> ApprovalIntent:
        return ApprovalIntent(
            owner=self.crud.repository.principal_id,
            audience="personal-contract-review",
            session=proposal.session_id,
            action="accept-agent-contract",
            resource=proposal.request_id,
            arguments_hash=arguments_hash(proposal.model_dump(mode="json")),
            contract_revision=proposal.revision,
            grant_revision=0,
            policy_revision=0,
        )

    def issue(self, session: str, request: str, provenance: str) -> str:
        """Issue an internal short-lived proof after trusted review.

        Raises:
            HeinzelError: If the proposal is already accepted.
        """
        proof = secrets.token_urlsafe(32)
        digest = approval_proof_hash(proof)
        with self.crud.repository.control_writer(session) as writer:
            proposal = self.current(session, request)
            if self.applied(session, request):
                msg = "This contract proposal is already accepted"
                raise HeinzelError(msg)
            now = self.crud.clock()
            record = ApprovalRecord(
                intent=self.intent(proposal),
                proof_hash=digest,
                nonce=secrets.token_hex(32),
                provenance=provenance[:128],
                decision="approve",
                issued_at=now,
                expires_at=now + timedelta(minutes=5),
            )
            writer.append("approval.issued", key=digest, data=record.model_dump(mode="json"))
        return proof

    def accept(self, session: str, request: str, proof: str) -> ScheduledAgentProposal:
        """Atomically record acceptance only; never activate or grant permissions."""
        proposal = self.crud.scheduled_agent_proposal(session, request)

        def current(_events: list[SessionEvent]) -> ApprovalIntent:
            if self.applied(session, request):
                msg = "This contract proposal is already accepted"
                raise HeinzelError(msg)
            return self.intent(self.current(session, request))

        def effect(
            writer: SessionWriter, _events: list[SessionEvent], _evidence: ApprovalEvidence
        ) -> dict[str, JsonValue]:
            body = immutable_body(writer, "accepted-agent-contract", proposal.model_dump(mode="json"), control=True)
            return body.model_dump(mode="json")

        with self.crud.repository.main_writer(session, wait=True):
            consume_approval(
                self.crud.repository,
                session,
                proof,
                self.intent(proposal),
                current,
                self.crud.clock,
                event_kind="agent.contract-accepted",
                effect_data=effect,
            )
        return proposal

    def applied(self, session: str, request: str) -> bool:
        """Verify persisted evidence for the exact accepted proposal.

        Raises:
            HeinzelError: If evidence is invalid.
        """
        events = self.crud.repository.control_read(session).events
        proposal = self.crud.scheduled_agent_proposal(session, request)
        intent = self.intent(proposal)
        for event in events:
            if event.kind != "agent.contract-accepted":
                continue
            body = ScheduledAgentProposal.model_validate(self.crud.read_body(session, event))
            if body != proposal:
                continue
            matches = [
                e
                for e in events
                if e.kind == "approval.issued" and e.key == event.data.get("proof_hash") and e.seq < event.seq
            ]
            if len(matches) != 1:
                msg = "The saved contract acceptance lacks its issuer evidence"
                raise HeinzelError(msg)
            try:
                record = ApprovalRecord.model_validate_json(json.dumps(matches[0].data))
            except ValueError:
                msg = "The saved contract acceptance has invalid issuer evidence"
                raise HeinzelError(msg) from None
            if (
                record.model_dump(mode="json").get("decision") != "approve"
                or not record.issued_at <= event.ts < record.expires_at
                or matches[0].ts > event.ts
                or record.expires_at <= record.issued_at
                or record.intent != intent
                or record.proof_hash != matches[0].key
                or record.nonce != event.data.get("consumed_nonce")
                or record.intent.model_dump(mode="json") != event.data.get("intent")
                or record.provenance != event.data.get("provenance")
                or sum(e.data.get("consumed_nonce") == record.nonce for e in events) != 1
                or any(e.kind == "approval.revoked" and e.key == record.nonce for e in events)
            ):
                msg = "The saved contract acceptance has invalid issuer evidence"
                raise HeinzelError(msg)
            return True
        return False
