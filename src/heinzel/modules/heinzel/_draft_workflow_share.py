"""Exact edited-section review and journaled human-approved link delivery (ADR 0127)."""

import hashlib
import json
import secrets
from datetime import timedelta

from pydantic import JsonValue

from heinzel import ChannelNotice, ChannelReceipt, DraftSectionSnapshot, HeinzelError, SessionEvent, SessionWriter
from heinzel.kernel._approval import (
    ApprovalConfirmation,
    ApprovalEvidence,
    ApprovalIntent,
    ApprovalRecord,
    approval_proof_hash,
    arguments_hash,
    consume_approval,
)
from heinzel.modules.heinzel._draft_workflow_effects import post_owner_notice, verified
from heinzel.modules.heinzel._draft_workflow_journal import grant_payload, matching, target_key
from heinzel.modules.heinzel._draft_workflow_models import DraftPublicationTerms, DraftShareReview
from heinzel.modules.heinzel._draft_workflow_publication import DraftWorkflowPublication, same_document
from heinzel.modules.heinzel._session_bodies import immutable_body


def share_payload(review: DraftShareReview) -> dict[str, JsonValue]:
    """Serialize the complete edited text for human review; SecretStr redaction is unsuitable for approval."""
    value = review.model_dump(mode="json")
    snapshot = review.snapshot.model_dump(mode="json")
    snapshot["text"] = review.snapshot.text.get_secret_value()
    value["snapshot"] = snapshot
    return value


class DraftWorkflowShare:
    """Keep editing, consent, transport attempt and acknowledgement separate and durable."""

    def __init__(self, host: DraftWorkflowPublication) -> None:
        self.host = host

    def capture(self, terms: DraftPublicationTerms, key: str) -> DraftSectionSnapshot:
        """Read only the acknowledged host draft, at its actual edited revision.

        Raises:
            HeinzelError: If the source, draft identity or bounded document scope changed.
        """
        host = self.host
        if len(matching(host.journal.events(terms.session_id), "draft.append-acknowledged", key)) != 1:
            msg = "Only an acknowledged draft can be reviewed for sharing"
            raise HeinzelError(msg)
        ports = verified(host, terms)
        snapshot = ports.append.call(lambda service: service.capture(terms.document, key))
        raw = snapshot.model_dump(mode="json")
        raw["text"] = snapshot.text.get_secret_value()
        snapshot = DraftSectionSnapshot.model_validate_json(json.dumps(raw))
        if snapshot.draft_key != key or not same_document(snapshot.target, terms.document):
            msg = "The edited draft's identity or document scope changed"
            raise HeinzelError(msg)
        return snapshot

    def propose(self, session: str, key: str) -> DraftShareReview:
        """Capture an edited section as an inert exact review; neither a read nor an edit approves delivery."""
        host = self.host
        host.owner(human=True)
        terms = host.published(session)
        snapshot = self.capture(terms, key)
        review = DraftShareReview(
            owner=terms.owner,
            session_id=session,
            publication_request=terms.request.request_id,
            generation=terms.generation,
            grant_sha256=arguments_hash(grant_payload(terms)),
            draft_key=key,
            snapshot=snapshot,
            content_sha256=hashlib.sha256(snapshot.text.get_secret_value().encode()).hexdigest(),
            channel=terms.request.publication_channel,
            channel_binding=terms.channel_binding,
            channel_target=terms.channel_target,
            link_text=terms.document.reference,
        )
        identity = arguments_hash(share_payload(review))
        host.journal.record(
            session,
            "draft.share-proposed",
            identity,
            share_payload(review),
            "The edited section and revision were captured for your exact link-sharing review.",
        )
        return review

    def current(self, session: str, request: str) -> DraftShareReview:
        """Recheck the exact stored revision, refusing edits after the human preview.

        Raises:
            HeinzelError: If review, grant, source or edited revision is no longer exact.
        """
        host = self.host
        host.owner(human=True)
        events = matching(host.journal.events(session), "draft.share-proposed", request)
        if len(events) != 1:
            msg = "The exact edited-draft share review is unavailable"
            raise HeinzelError(msg)
        review = DraftShareReview.model_validate_json(json.dumps(host.crud.read_body(session, events[0])))
        terms = host.published(session)
        if (
            arguments_hash(share_payload(review)) != request
            or review.owner != terms.owner
            or review.session_id != session
            or review.grant_sha256 != arguments_hash(grant_payload(terms))
            or self.capture(terms, review.draft_key) != review.snapshot
        ):
            msg = "The edited draft or reviewed publication changed; review its current revision again"
            raise HeinzelError(msg)
        return review

    @staticmethod
    def intent(review: DraftShareReview) -> ApprovalIntent:
        """Bind the complete edited snapshot and exact channel target into one human decision."""
        return ApprovalIntent(
            owner=review.owner,
            audience="draft-workflow-share",
            session=review.session_id,
            action="share-draft-link",
            resource=review.draft_key,
            arguments_hash=arguments_hash(share_payload(review)),
            contract_revision=0,
            grant_revision=review.generation,
            policy_revision=1,
        )

    def issue(self, session: str, request: str, provenance: str) -> str:
        """Issue only inside an actual trusted owner confirmation surface.

        Raises:
            HeinzelError: If provenance, snapshot or current authority is invalid.
        """
        if not provenance.startswith(("chat-confirm:", "cli-publish:")):
            msg = "Sharing requires the trusted human confirmation surface"
            raise HeinzelError(msg)
        review = self.current(session, request)
        if matching(self.host.journal.events(session), "draft.share-attempted", review.draft_key):
            msg = "This draft's share was already attempted and cannot be repeated"
            raise HeinzelError(msg)
        proof = secrets.token_urlsafe(32)
        digest = approval_proof_hash(proof)
        now = self.host.crud.clock()
        record = ApprovalRecord(
            intent=self.intent(review),
            proof_hash=digest,
            nonce=digest,
            provenance=provenance,
            decision="approve",
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        with self.host.journal.repository.control_writer(session) as writer:
            writer.append("approval.issued", key=digest, data=record.model_dump(mode="json"))
        return proof

    def applied(self, session: str, request: str, payload: JsonValue) -> bool:
        """Read historical acknowledgement without requiring the still-mutable document to remain unchanged."""
        self.host.owner(human=True, read=True)
        if not isinstance(payload, dict) or arguments_hash(payload) != request:
            return False
        review = DraftShareReview.model_validate_json(json.dumps(payload))
        events = self.host.journal.events(session)
        approvals = matching(events, "draft.share-approved", "share-approval-" + request)
        receipts = matching(events, "draft.share-acknowledged", review.draft_key)
        if len(approvals) != 1 or len(receipts) != 1:
            return False
        receipt = self.host.crud.read_body(session, receipts[0])
        return isinstance(receipt, dict) and receipt.get("review") == payload

    def execute(
        self, session: str, request: str, payload: dict[str, JsonValue], proof: str, confirmation: ApprovalConfirmation
    ) -> str:
        """Consume exact human consent before one durable link attempt; unknown outcomes never repost.

        Raises:
            HeinzelError: If consent or revision changed, or the transport outcome is uncertain.
        """
        host = self.host
        args: dict[str, JsonValue] = {
            "session_id": session,
            "request_id": request,
            "review": payload,
            "approval_proof": proof,
        }
        if not confirmation.matches("heinzel", "share-draft-link", args):
            msg = "Sharing approval is invalid or unavailable"
            raise HeinzelError(msg)
        review = self.current(session, request)
        if arguments_hash(payload) != arguments_hash(share_payload(review)):
            msg = "The human-reviewed draft changed"
            raise HeinzelError(msg)
        terms = host.published(session)
        key = review.draft_key

        def current(_events: list[SessionEvent]) -> ApprovalIntent:
            return self.intent(self.current(session, request))

        def effect(
            writer: SessionWriter, _events: list[SessionEvent], _evidence: ApprovalEvidence
        ) -> dict[str, JsonValue]:
            body = immutable_body(writer, "approved-draft-share", payload, control=True)
            return {"request_id": request, **body.model_dump(mode="json")}

        with (
            host.journal.repository.draft_target_writer(session, target_key(terms), wait=True) as fence,
            host.journal.repository.main_writer(session, wait=True),
        ):
            if matching(host.journal.events(session), "draft.share-attempted", key):
                msg = "This share was already attempted; its receipt must be inspected without reposting"
                raise HeinzelError(msg)
            consume_approval(
                host.journal.repository,
                session,
                proof,
                self.intent(review),
                current,
                host.crud.clock,
                event_kind="draft.share-approved",
                event_key="share-approval-" + request,
                idempotency_hash=arguments_hash(payload),
                effect_data=effect,
            )
            host.journal.record(
                session,
                "draft.share-attempted",
                key,
                {"review": payload},
                "You approved this exact edited revision. One document-link share was attempted.",
            )
            try:
                # A final capture after durable consent also catches edits during the confirmation boundary.
                self.current(session, request)
                ports = verified(host, terms)
                notice = ChannelNotice(
                    key=arguments_hash(["draft-share", key]),
                    helper=terms.request.name,
                    title="Reviewed release poem",
                    body=review.link_text,
                    route="/heinzel?chat=" + session,
                )
                receipt = ports.share.call(lambda service: service.deliver_link_reviewed(notice, review.channel_target))
                receipt = ChannelReceipt.model_validate_json(receipt.model_dump_json())
                if (
                    receipt.channel != review.channel
                    or receipt.status == "unknown"
                    or ports.share.call(lambda service: service.delivery_target()) != review.channel_target
                ):
                    msg = "The exact link-sharing acknowledgement is unavailable"
                    raise HeinzelError(msg)  # noqa: TRY301 - isolated post-effect receipt boundary
            except Exception:  # noqa: BLE001 - a failed call may follow a real remote delivery
                host.journal.record(
                    session,
                    "draft.share-uncertain",
                    key,
                    {"status": "unknown"},
                    "The share outcome is uncertain. It will never be automatically reposted.",
                )
                msg = "The link-sharing outcome is uncertain; inspect it without repeating delivery"
                raise HeinzelError(msg) from None
            host.journal.record(
                session,
                "draft.share-acknowledged",
                key,
                {"status": receipt.status, "reference": receipt.reference, "review": payload},
                "The link transport reported " + receipt.status + " for your reviewed edited revision.",
            )
            if receipt.status in {"queued", "delivered"}:
                post_owner_notice(host, terms, key, fence, confirmation=True)
            return receipt.status
