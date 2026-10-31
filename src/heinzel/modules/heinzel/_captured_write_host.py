"""One exact conditional write into a document: drafted by a turn, approved by its owner, attempted once."""

import hashlib
import json
import secrets
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, Field, JsonValue

from heinzel import CurrentUser, HeinzelError, SessionEvent, SessionWriter
from heinzel.kernel import CapturedWriteDraft, CapturedWriteReview, CapturedWriteService
from heinzel.kernel._approval import (
    ApprovalConfirmation,
    ApprovalEvidence,
    ApprovalIntent,
    ApprovalRecord,
    approval_proof_hash,
    arguments_hash,
    consume_approval,
)
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud

_MAX_REVISION = 256


class CapturedWriteReceipt(BaseModel):
    """Host-recorded provider acknowledgement of one exact attempt; not a later read observation."""

    request_id: str = Field(description="Owner-scoped identity of the confirmed write draft.")
    provider: str = Field(description="Host-selected provider recorded with the draft.")
    account: str = Field(description="Account recorded in the reviewed draft.")
    source: str = Field(description="Source identity recorded in the reviewed draft.")
    reference: str = Field(description="Exact artifact reference recorded in the reviewed draft.")
    base_revision: str = Field(description="Revision reviewed before the attempt.")
    revision: str | None = Field(default=None, description="Revision returned by the provider acknowledgement, if any.")


class CapturedWriteStatus(BaseModel):
    """Owner-private state without draft content or an invented success."""

    status: Literal["draft", "uncertain", "acknowledged"] = Field(
        description="Local state; a draft does not grant a write."
    )
    receipt: CapturedWriteReceipt | None = Field(
        default=None, description="Recorded provider acknowledgement, not a later artifact verification, if present."
    )


def review_payload(draft: CapturedWriteDraft) -> dict[str, JsonValue]:
    """Return exact private draft terms for the authenticated human review boundary."""
    return draft.model_dump(mode="json") | {"content": draft.content.get_secret_value()}


def _expected(draft: CapturedWriteDraft) -> CapturedWriteReview:
    return CapturedWriteReview(
        owner=draft.owner,
        session=draft.session,
        provider=draft.provider,
        binding_id=draft.binding_id,
        account=draft.account,
        source=draft.source,
        reference=draft.reference,
        operation=draft.operation,
        section=draft.section,
        base_revision=draft.base_revision,
        content_sha256=hashlib.sha256(draft.content.get_secret_value().encode("utf-8")).hexdigest(),
        write_scope=draft.write_scope,
        conditional_mutation=True,
    )


class CapturedWriteHost:
    """Internal state machine; a future host must bind its service to an actual selected provider record."""

    def __init__(self, crud: SessionCrud, current: CurrentUser, provider: CapturedWriteService) -> None:
        self.crud, self.user, self.provider = crud, current, provider

    def _owner(self, draft: CapturedWriteDraft, *, drafting: bool = False) -> None:
        heads = {"cli", "api-bearer", "web-session", "web-device"} | ({"daemon"} if drafting else set[str]())
        context = self.user.context()
        if (
            context.principal != self.user.owner()
            or context.principal.id != self.crud.repository.principal_id
            or context.principal.id != draft.owner
            or context.authentication not in heads
            or "private:write" not in context.permissions
        ):
            msg = "The captured write requires its authenticated owner"
            raise HeinzelError(msg)

    def draft(self, draft: CapturedWriteDraft) -> None:
        """Persist an inert exact private draft without opening a provider lease.

        Raises:
            HeinzelError: If the owner or saved draft does not match.
        """
        self._owner(draft)
        with (
            self.crud.repository.main_writer(draft.session),
            self.crud.repository.control_writer(draft.session) as writer,
        ):
            prior = [
                e
                for e in self.crud.repository.control_read(draft.session).events
                if e.kind == "write.drafted" and e.key == draft.request_id
            ]
            if prior:
                if len(prior) != 1 or self._read(draft.session, prior[0]) != draft:
                    msg = "This draft request already identifies different terms"
                    raise HeinzelError(msg)
                return
            body = immutable_body(writer, "captured-write-draft", review_payload(draft), control=True)
            writer.append("write.drafted", key=draft.request_id, data=body.model_dump(mode="json"))

    def draft_in_turn(self, draft: CapturedWriteDraft, turn: str) -> None:
        """Record a turn's inert draft; the turn already holds this session's main writer."""
        self._owner(draft, drafting=True)
        with self.crud.repository.control_writer(draft.session) as writer:
            body = immutable_body(writer, "captured-write-draft", review_payload(draft), control=True)
            writer.append("write.drafted", key=draft.request_id, turn=turn, data=body.model_dump(mode="json"))

    def discard(self, session: str, request: str) -> None:
        """Set a draft aside for good; an attempted write stays as it is.

        Raises:
            HeinzelError: If the draft is unavailable or was already attempted.
        """
        with self.crud.repository.main_writer(session):
            self.current(session, request)
            if self._attempts(session, request):
                msg = "This write was already attempted; inspect its outcome"
                raise HeinzelError(msg)
            with self.crud.repository.control_writer(session) as writer:
                writer.append("write.discarded", key=request, data={})

    def pending(self, session: str) -> list[dict[str, JsonValue]]:
        """Drafts still waiting for their owner, oldest first, with exact review terms."""
        events = self.crud.repository.control_read(session).events
        settled = {e.key for e in events if e.kind == "write.discarded"} | {
            e.key.removeprefix("write-attempt-") for e in events if e.kind == "write.attempted" and e.key
        }
        return [
            review_payload(self._read(session, e)) for e in events if e.kind == "write.drafted" and e.key not in settled
        ]

    def _read(self, session: str, event: SessionEvent) -> CapturedWriteDraft:
        try:
            draft = CapturedWriteDraft.model_validate(self.crud.read_body(session, event))
        except (ValueError, TypeError):
            msg = "The captured write draft is invalid"
            raise HeinzelError(msg) from None
        if draft.session != session or event.key != draft.request_id:
            msg = "The captured write draft is invalid"
            raise HeinzelError(msg)
        self._owner(draft)
        return draft

    def current(self, session: str, request: str) -> CapturedWriteDraft:
        events = self.crud.repository.control_read(session).events
        drafts = [event for event in events if event.kind == "write.drafted" and event.key == request]
        if len(drafts) != 1:
            msg = "The captured write draft is unavailable"
            raise HeinzelError(msg)
        return self._read(session, drafts[0])

    @staticmethod
    def _intent(draft: CapturedWriteDraft) -> ApprovalIntent:
        return ApprovalIntent(
            owner=draft.owner,
            audience="captured-artifact-write",
            session=draft.session,
            action="execute-captured-write",
            resource=draft.request_id,
            arguments_hash=arguments_hash(review_payload(draft)),
            contract_revision=0,
            grant_revision=0,
            policy_revision=0,
        )

    def _observe(self, draft: CapturedWriteDraft) -> None:
        try:
            lease = self.provider.open_lease(draft)
            try:
                if lease.review() != _expected(draft):
                    msg = "The captured write target, scope or revision changed"
                    raise HeinzelError(msg)
            finally:
                lease.close()
        except Exception:  # noqa: BLE001 - provider failures may contain private credentials
            msg = "The document or connection changed since this draft; nothing was written. Set it aside and ask again"
            raise HeinzelError(msg) from None

    def review(self, session: str, request: str) -> dict[str, JsonValue]:
        """Re-observe the selected target and return exact owner-private human review terms.

        Raises:
            HeinzelError: If current target or scope cannot be verified.
        """
        with self.crud.repository.main_writer(session):
            draft = self.current(session, request)
            self._observe(draft)
            return review_payload(draft)

    def issue(self, session: str, request: str, provenance: str) -> str:
        """Internal issuer: call only from a trusted human review boundary.

        Raises:
            HeinzelError: If current terms or saved state do not match.
        """
        with self.crud.repository.main_writer(session):
            draft = self.current(session, request)
            self._observe(draft)
            proof = secrets.token_urlsafe(32)
            digest = approval_proof_hash(proof)
            with self.crud.repository.control_writer(session) as writer:
                if self.current(session, request) != draft or self._attempts(session, request):
                    msg = "The captured write changed or was already attempted"
                    raise HeinzelError(msg)
                now = self.crud.clock()
                record = ApprovalRecord(
                    intent=self._intent(draft),
                    proof_hash=digest,
                    nonce=secrets.token_hex(32),
                    provenance=provenance[:128],
                    decision="approve",
                    issued_at=now,
                    expires_at=now + timedelta(minutes=5),
                )
                writer.append("approval.issued", key=digest, data=record.model_dump(mode="json"))
            return proof

    def _attempts(self, session: str, request: str) -> list[SessionEvent]:
        return [
            e
            for e in self.crud.repository.control_read(session).events
            if e.kind == "write.attempted" and e.key == f"write-attempt-{request}"
        ]

    def status(self, session: str, request: str) -> Literal["draft", "uncertain", "acknowledged"]:
        """Reconcile the private journal without repeating a provider operation.

        Raises:
            HeinzelError: If saved attempt or acknowledgement evidence is inconsistent.
        """
        draft = self.current(session, request)
        events = self.crud.repository.control_read(session).events
        drafts = [event for event in events if event.kind == "write.drafted" and event.key == request]
        attempts = [
            event for event in events if event.kind == "write.attempted" and event.key == f"write-attempt-{request}"
        ]
        receipts = [event for event in events if event.kind == "write.acknowledged" and event.key == request]
        invalid = "The captured write evidence is invalid"
        if not attempts:
            if receipts:
                raise HeinzelError(invalid)
            return "draft"
        if len(drafts) != 1 or len(attempts) != 1 or len(receipts) > 1:
            raise HeinzelError(invalid)
        attempt = attempts[0]
        issued = [
            event for event in events if event.kind == "approval.issued" and event.key == attempt.data.get("proof_hash")
        ]
        if len(issued) != 1:
            raise HeinzelError(invalid)
        try:
            record = ApprovalRecord.model_validate_json(json.dumps(issued[0].data))
            saved = CapturedWriteDraft.model_validate(self.crud.read_body(session, attempt))
        except (ValueError, TypeError):
            raise HeinzelError(invalid) from None
        if (
            saved != draft
            or record.intent != self._intent(draft)
            or record.proof_hash != issued[0].key
            or record.nonce != attempt.data.get("consumed_nonce")
            or record.provenance != attempt.data.get("provenance")
            or record.intent.model_dump(mode="json") != attempt.data.get("intent")
            or attempt.data.get("request_hash") != arguments_hash(review_payload(draft))
            or not drafts[0].seq < issued[0].seq < attempt.seq
            or issued[0].ts > attempt.ts
            or not record.issued_at <= attempt.ts < record.expires_at
            or sum(event.data.get("consumed_nonce") == record.nonce for event in events) != 1
            or any(
                event.kind == "approval.revoked" and event.key == record.nonce and event.seq < attempt.seq
                for event in events
            )
        ):
            raise HeinzelError(invalid)
        if not receipts:
            return "uncertain"
        receipt = receipts[0]
        revision = receipt.data.get("revision")
        if (
            receipt.seq <= attempt.seq
            or receipt.data.get("attempt_seq") != attempt.seq
            or receipt.data.get("draft_sha256") != arguments_hash(review_payload(draft))
            or receipt.data.get("reference") != draft.reference
            or not (revision is None or (isinstance(revision, str) and 0 < len(revision) <= _MAX_REVISION))
        ):
            raise HeinzelError(invalid)
        return "acknowledged"

    def projection(self, session: str, request: str) -> CapturedWriteStatus:
        """Project only verified acknowledgement evidence for the owner.

        Raises:
            HeinzelError: If the journal evidence is incomplete or inconsistent.
        """
        status = self.status(session, request)
        if status != "acknowledged":
            return CapturedWriteStatus(status=status)
        draft = self.current(session, request)
        receipts = [
            event
            for event in self.crud.repository.control_read(session).events
            if event.kind == "write.acknowledged" and event.key == request
        ]
        if len(receipts) != 1:
            msg = "The captured write receipt is unavailable"
            raise HeinzelError(msg)
        revision = receipts[0].data.get("revision")
        if revision is not None and not isinstance(revision, str):
            msg = "The captured write receipt is invalid"
            raise HeinzelError(msg)
        return CapturedWriteStatus(
            status=status,
            receipt=CapturedWriteReceipt(
                request_id=request,
                provider=draft.provider,
                account=draft.account,
                source=draft.source,
                reference=draft.reference,
                base_revision=draft.base_revision,
                revision=revision,
            ),
        )

    def execute(
        self, session: str, request: str, review: dict[str, JsonValue], proof: str, confirmation: ApprovalConfirmation
    ) -> str:
        """Consume exact trusted confirmation and attempt once; failures remain uncertain.

        Raises:
            HeinzelError: If confirmation, source, write, cleanup or receipt cannot be verified.
        """
        args: dict[str, JsonValue] = {
            "session_id": session,
            "request_id": request,
            "review": review,
            "approval_proof": proof,
        }
        if not confirmation.matches("heinzel", "execute-captured-write", args):
            msg = "The captured write requires trusted human confirmation"
            raise HeinzelError(msg)
        with self.crud.repository.main_writer(session, wait=True):
            draft = self.current(session, request)
            if review != review_payload(draft):
                msg = "The reviewed write draft changed"
                raise HeinzelError(msg)
            if self._attempts(session, request):
                msg = "This write was already attempted; inspect its outcome"
                raise HeinzelError(msg)

            def intent(_events: list[SessionEvent]) -> ApprovalIntent:
                if self._attempts(session, request):
                    msg = "This write was already attempted; inspect its outcome"
                    raise HeinzelError(msg)
                return self._intent(self.current(session, request))

            def effect(
                writer: SessionWriter, _events: list[SessionEvent], _evidence: ApprovalEvidence
            ) -> dict[str, JsonValue]:
                return immutable_body(writer, "captured-write-attempt", review_payload(draft), control=True).model_dump(
                    mode="json"
                ) | {"request_hash": arguments_hash(review_payload(draft))}

            consumed = consume_approval(
                self.crud.repository,
                session,
                proof,
                self._intent(draft),
                intent,
                self.crud.clock,
                event_kind="write.attempted",
                event_key=f"write-attempt-{request}",
                idempotency_hash=arguments_hash(review_payload(draft)),
                effect_data=effect,
            )
            if consumed.disposition != "recorded":
                msg = "This write was already attempted; inspect its outcome"
                raise HeinzelError(msg)
            self._perform(draft, consumed.sequence)
            return "acknowledged"

    def _perform(self, draft: CapturedWriteDraft, attempt_seq: int) -> None:
        try:
            lease = self.provider.open_lease(draft)
            try:
                if lease.review() != _expected(draft):
                    msg = "The captured write target, scope or revision changed"
                    raise HeinzelError(msg)
                acknowledgement = lease.write()
                if acknowledgement.reference != draft.reference:
                    msg = "The provider acknowledged a different resource"
                    raise HeinzelError(msg)
                observed = lease.review()
                if observed.model_copy(update={"base_revision": draft.base_revision}) != _expected(draft) or (
                    acknowledgement.revision is not None and observed.base_revision != acknowledgement.revision
                ):
                    msg = "The captured source or account changed during the write"
                    raise HeinzelError(msg)
            finally:
                lease.close()
            with self.crud.repository.control_writer(draft.session) as writer:
                writer.append(
                    "write.acknowledged",
                    key=draft.request_id,
                    data={
                        "attempt_seq": attempt_seq,
                        "draft_sha256": arguments_hash(review_payload(draft)),
                        "reference": acknowledgement.reference,
                        "revision": acknowledgement.revision,
                    },
                )
        except Exception:  # noqa: BLE001 - uncertain provider outcomes must be redacted and never retried
            msg = "The write outcome is uncertain; inspect the provider before any new review"
            raise HeinzelError(msg) from None
