"""Internal human-proof lifecycle for a whole read set; no provider execution or registered operation."""

import json
import secrets
from datetime import timedelta

from pydantic import JsonValue

from heinzel import HeinzelError, SessionEvent, SessionWriter
from heinzel.kernel._approval import (
    ApprovalConfirmation,
    ApprovalEvidence,
    ApprovalIntent,
    ApprovalRecord,
    approval_proof_hash,
    arguments_hash,
    consume_approval,
)
from heinzel.modules.heinzel._connected_read_set_reviews import ConnectedReadSetReviews, ReadSetReview, read_set_intent
from heinzel.modules.heinzel._session_bodies import immutable_body


class ConnectedReadSetApprovals:
    """Keep separate human confirmation and whole-set approval in the owner journal."""

    def __init__(self, reviews: ConnectedReadSetReviews) -> None:
        self.reviews = reviews
        self.sets = reviews.sets
        self.crud = self.sets.crud

    def issue(self, session: str, request: str, revision: int, provenance: str) -> str:
        """Issue an internal short-lived proof after a trusted human confirms this exact review.

        Raises:
            HeinzelError: If the review, account, owner or source is no longer current.
        """
        self.sets.latest(session)
        proof = secrets.token_urlsafe(32)
        digest = approval_proof_hash(proof)
        with self.crud.repository.main_writer(session) as main:
            review, intent = self.reviews.review_intent_locked(session, main, request, revision)
            with self.crud.repository.control_writer(session) as writer:
                events = self.crud.repository.control_read(session).events
                if any(
                    event.kind == "connected-set.approved"
                    and ReadSetReview.model_validate(self.crud.read_body(session, event)).request_id
                    == review.request_id
                    for event in events
                ):
                    msg = "This read-set review was already approved"
                    raise HeinzelError(msg)
                now = self.crud.clock()
                record = ApprovalRecord(
                    intent=intent,
                    proof_hash=digest,
                    nonce=secrets.token_hex(32),
                    provenance=provenance[:128],
                    decision="approve",
                    issued_at=now,
                    expires_at=now + timedelta(minutes=5),
                )
                writer.append("approval.issued", key=digest, data=record.model_dump(mode="json"))
        return proof

    def approve(
        self,
        session: str,
        request: str,
        review: dict[str, JsonValue],
        proof: str,
        confirmation: ApprovalConfirmation,
    ) -> ReadSetReview:
        """Consume a matching human confirmation and one-use proof; never run a source read.

        Raises:
            HeinzelError: If exact confirmation, proof or current terms are invalid.
        """
        self.sets.latest(session)
        revision = ReadSetReview.model_validate(review).revision
        args: dict[str, JsonValue] = {
            "session_id": session,
            "request_id": request,
            "review": review,
            "approval_proof": proof,
        }
        if not confirmation.matches("heinzel", "approve-connected-read-set", args):
            msg = "Approval is invalid or unavailable"
            raise HeinzelError(msg)
        with self.crud.repository.main_writer(session) as main:
            actual, intent = self.reviews.review_intent_locked(session, main, request, revision)
            if arguments_hash(actual.model_dump(mode="json")) != arguments_hash(review):
                msg = "The reviewed read set changed"
                raise HeinzelError(msg)

            def current(events: list[SessionEvent]) -> ApprovalIntent:
                if any(
                    event.kind == "connected-set.approved"
                    and ReadSetReview.model_validate(self.crud.read_body(session, event)).request_id == request
                    for event in events
                ):
                    msg = "This read-set review was already approved"
                    raise HeinzelError(msg)
                return self.reviews.review_intent_locked(session, main, request, revision)[1]

            def effect(
                writer: SessionWriter, _events: list[SessionEvent], _evidence: ApprovalEvidence
            ) -> dict[str, JsonValue]:
                body = immutable_body(writer, "approved-read-set", actual.model_dump(mode="json"), control=True)
                return body.model_dump(mode="json")

            result = consume_approval(
                self.crud.repository,
                session,
                proof,
                intent,
                current,
                self.crud.clock,
                event_kind="connected-set.approved",
                effect_data=effect,
            )
            if result.disposition != "recorded":
                msg = "This read-set proof was already consumed"
                raise HeinzelError(msg)
            return actual.model_copy(deep=True)

    def approved(self, session: str) -> ReadSetReview:
        """Project exact latest unrevoked approval evidence; this does not authorize a provider read.

        Raises:
            HeinzelError: If its stored human evidence or current reviewed set is invalid.
        """
        self.sets.latest(session)
        events = self.crud.repository.control_read(session).events
        approvals = [event for event in events if event.kind == "connected-set.approved"]
        if not approvals:
            msg = "No connected read set has been approved"
            raise HeinzelError(msg)
        event = approvals[-1]
        grant = ReadSetReview.model_validate(self.crud.read_body(session, event))
        reviewed = [e for e in events if e.kind == "connected-set.reviewed" and e.key == grant.request_id]
        issued = [e for e in events if e.kind == "approval.issued" and e.key == event.data.get("proof_hash")]
        latest = [e for e in events if e.kind == "connected-set.reviewed"]
        if len(reviewed) != 1 or len(issued) != 1 or not reviewed[0].seq < issued[0].seq < event.seq:
            msg = "The connected read-set approval evidence is invalid"
            raise HeinzelError(msg)
        try:
            record = ApprovalRecord.model_validate_json(json.dumps(issued[0].data))
        except ValueError:
            msg = "The connected read-set approval evidence is invalid"
            raise HeinzelError(msg) from None
        current = self.sets.latest(session)
        if (
            event.key != record.nonce
            or grant.proposal.owner != self.sets.owner
            or grant.proposal.session != session
            or current is None
            or arguments_hash(grant.proposal.model_dump(mode="json")) != arguments_hash(current.model_dump(mode="json"))
            or not latest
            or latest[-1].key != grant.request_id
            or arguments_hash(self.crud.read_body(session, reviewed[0]))
            != arguments_hash(grant.model_dump(mode="json"))
            or not record.issued_at <= event.ts < record.expires_at
            or issued[0].ts > event.ts
            or record.intent != read_set_intent(grant)
            or record.proof_hash != issued[0].key
            or record.nonce != event.data.get("consumed_nonce")
            or record.provenance != event.data.get("provenance")
            or record.intent.model_dump(mode="json") != event.data.get("intent")
            or sum(e.data.get("consumed_nonce") == record.nonce for e in events) != 1
            or any(e.kind == "approval.revoked" and e.key == record.nonce for e in events)
            or any(e.kind == "connected-set.revoked" and e.key == grant.request_id for e in events)
        ):
            msg = "The connected read-set approval evidence is invalid or revoked"
            raise HeinzelError(msg)
        return grant

    def revoke(self, session: str, request: str) -> None:
        """Revoke the whole exact active set under the owner turn fence.

        Raises:
            HeinzelError: If its current approval does not match.
        """
        self.sets.latest(session)
        with self.crud.repository.main_writer(session), self.crud.repository.control_writer(session) as writer:
            if self.approved(session).request_id != request:
                msg = "The read-set approval changed"
                raise HeinzelError(msg)
            writer.append("connected-set.revoked", key=request, data={})
