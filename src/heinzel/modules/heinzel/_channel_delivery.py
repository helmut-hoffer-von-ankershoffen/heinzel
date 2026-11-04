"""Exact per-helper channel consent, durable outbox and host-verified reply consumption (ADR 0120).

No plugin reply object is authority. Only this host reads its own posted journal, checks its pinned source,
destination and owner identity, rechecks exact pending captured-write terms, then records a claimed decision before
entering the existing one-use proof boundary. A crash after an attempt or decision claim is never replayed.
"""

import json
import secrets
from datetime import timedelta
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from heinzel import (
    ApprovalChannel,
    ChannelApprovalReply,
    ChannelApprovalRequest,
    ChannelNotice,
    ChannelReceipt,
    ChannelReplyConsumer,
    ChannelTarget,
    CurrentUser,
    HeinzelError,
    Kernel,
    NotificationChannel,
    SessionEvent,
)
from heinzel.kernel._approval import (
    ApprovalConfirmation,
    ApprovalIntent,
    ApprovalRecord,
    approval_proof_hash,
    arguments_hash,
    consume_approval,
)
from heinzel.kernel._features import feature_enabled
from heinzel.modules.heinzel._captured_write_binding import SelectedWriteService
from heinzel.modules.heinzel._captured_write_host import CapturedWriteHost
from heinzel.modules.heinzel._channel_binding import ChannelBinding
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import RequestId, SessionId

_MAX_PREVIEW = 4000


class ChannelDeliveryTerms(BaseModel):
    """An inert exact per-item transport proposal, including the full preview and linked owner."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: SessionId = Field(description="The owning durable personal chat identity.")
    request_id: RequestId = Field(
        description="Stable identity for this exact request; reuse only for an unchanged retry."
    )
    owner: str = Field(description="The authenticated installation owner's private identity.")
    channel: str = Field(description="The selected registered channel name.", pattern=r"^[a-z][a-z0-9-]{0,63}$")
    kind: Literal["notice", "approval"] = Field(
        description="Whether this proposal posts a notice or requests approval."
    )
    binding_id: str = Field(description="Digest identifying the captured transport source.", pattern=r"^[0-9a-f]{64}$")
    target: ChannelTarget = Field(description="The exact observed provider account, resource, revision and scope.")
    actor: str | None = Field(
        description="The exact linked owner actor, when approval replies are supported.", default=None, max_length=256
    )
    notice: ChannelNotice | None = Field(default=None, description="The complete proposed notice, when applicable.")
    approval: ChannelApprovalRequest | None = Field(
        default=None, description="The exact bounded human approval request, when applicable."
    )
    write_request: RequestId | None = Field(
        default=None, description="The linked captured-write request, when applicable."
    )
    write_review: dict[str, JsonValue] | None = Field(
        default=None, description="The complete exact captured-write review, when applicable."
    )


class ChannelDelivery(ChannelReplyConsumer):
    """Host-only consumer; no channel or model can inject a verified outcome through an operation parameter."""

    def __init__(self, crud: SessionCrud, current: CurrentUser, kernel: Kernel) -> None:
        """Keep authority in the owner's private journal and resolve source continuity on each use."""
        self.crud, self.current, self.kernel = crud, current, kernel

    def _owner(self, *, daemon: bool = False) -> None:
        context = self.current.context()
        if (
            context.principal != self.current.owner()
            or context.principal.id != self.crud.repository.principal_id
            or context.authentication not in ({"cli", "web-session", "web-device"} | ({"daemon"} if daemon else set()))
            or "private:write" not in context.permissions
        ):
            msg = "Channel actions require the authenticated owner and a trusted human confirmation"
            raise HeinzelError(msg)

    def _write_host(self, marker: ApprovalConfirmation | None = None) -> CapturedWriteHost:
        return CapturedWriteHost(
            self.crud, self.current, self.kernel.resolve(SelectedWriteService), channel_confirmation=marker
        )

    def propose(
        self,
        session: str,
        request: str,
        channel: str,
        *,
        notice: ChannelNotice | None = None,
        write_request: str | None = None,
    ) -> ChannelDeliveryTerms:
        """Capture exact posting terms for separate human review, without posting anything.

        Raises:
            HeinzelError: If the owner, transport, pending draft or request identity is invalid.
        """
        self._owner()
        if (notice is None) == (write_request is None):
            msg = "Choose one notice or one exact captured-write review"
            raise HeinzelError(msg)
        binding = ChannelBinding(self.kernel, channel, "notice" if notice else "approval")
        review = self._write_host().review(session, write_request) if write_request else None
        approval = None
        if write_request is not None and review is not None:
            preview = json.dumps(review, ensure_ascii=False, sort_keys=True, indent=2)
            if len(preview) > _MAX_PREVIEW:
                msg = "The full draft must fit the channel's exact preview; review it in the app instead"
                raise HeinzelError(msg)
            approval = ChannelApprovalRequest(
                request_id=arguments_hash([session, request]),
                helper=self.crud.snapshot(session).name,
                action="Insert this exact reviewed draft",
                preview=preview,
                arguments_sha256=arguments_hash(review),
                expires_at=self.crud.clock() + timedelta(hours=24),
            )
        if notice is not None:
            notice = ChannelNotice.model_validate(
                notice.model_dump()
                | {"key": arguments_hash([session, request]), "helper": self.crud.snapshot(session).name}
            )
        terms = ChannelDeliveryTerms(
            session_id=session,
            request_id=request,
            owner=self.current.principal().id,
            channel=channel,
            kind="notice" if notice else "approval",
            binding_id=binding.binding_id,
            target=binding.target(),
            actor=binding.actor() if approval else None,
            notice=notice,
            approval=approval,
            write_request=write_request,
            write_review=review,
        )
        if approval and not terms.actor:
            msg = "Link the owner's signed-in channel identity before requesting approval"
            raise HeinzelError(msg)
        with self.crud.repository.control_writer(session) as writer:
            prior = self._events(session, "channel.proposed", request)
            if prior:
                saved = self._terms(session, request)
                # Expiry is selected once by the host, never refreshed by repeated proposals.
                if saved.approval and terms.approval:
                    terms = terms.model_copy(
                        update={"approval": terms.approval.model_copy(update={"expires_at": saved.approval.expires_at})}
                    )
                if terms != saved:
                    msg = "This channel request already identifies different terms"
                    raise HeinzelError(msg)
                return saved
            body = immutable_body(writer, "channel-proposal", terms.model_dump(mode="json"), control=True)
            writer.append("channel.proposed", key=request, data=body.model_dump(mode="json"))
        return terms

    def _events(self, session: str, kind: str, request: str) -> list[SessionEvent]:
        key = f"channel-attempt-{request}" if kind == "channel.attempted" else request
        return [e for e in self.crud.repository.control_read(session).events if e.kind == kind and e.key == key]

    def _terms(self, session: str, request: str) -> ChannelDeliveryTerms:
        events = self._events(session, "channel.proposed", request)
        if len(events) != 1:
            msg = "The channel proposal is unavailable"
            raise HeinzelError(msg)
        terms = ChannelDeliveryTerms.model_validate(self.crud.read_body(session, events[0]))
        if terms.session_id != session or terms.request_id != request or terms.owner != self.current.principal().id:
            msg = "The channel proposal owner or identity changed"
            raise HeinzelError(msg)
        return terms

    def _binding(self, terms: ChannelDeliveryTerms) -> ChannelBinding:
        binding = ChannelBinding(self.kernel, terms.channel, terms.kind)
        if (
            binding.binding_id != terms.binding_id
            or binding.target() != terms.target
            or (terms.approval is not None and binding.actor() != terms.actor)
        ):
            msg = "The channel source, destination, scope or linked owner changed"
            raise HeinzelError(msg)
        return binding

    def review(self, session: str, request: str) -> dict[str, JsonValue]:
        """Read exact current terms without issuing consent or contacting the posting method.

        Raises:
            HeinzelError: If its current source, owner or pending draft no longer matches.
        """
        self._owner()
        terms = self._terms(session, request)
        self._binding(terms)
        if terms.approval is not None and (
            self.crud.clock() >= terms.approval.expires_at
            or self._write_host().review(session, cast("str", terms.write_request)) != terms.write_review
        ):
            msg = "The pending channel draft changed or expired"
            raise HeinzelError(msg)
        return terms.model_dump(mode="json")

    @staticmethod
    def _intent(terms: ChannelDeliveryTerms) -> ApprovalIntent:
        return ApprovalIntent(
            owner=terms.owner,
            audience="channel-delivery",
            session=terms.session_id,
            action="deliver-channel-item",
            resource=terms.request_id,
            arguments_hash=arguments_hash(terms.model_dump(mode="json")),
            contract_revision=0,
            grant_revision=0,
            policy_revision=0,
        )

    def issue(self, session: str, request: str, provenance: str) -> str:
        """Internal human boundary: issue exact transport consent, never approval of the captured write.

        Raises:
            HeinzelError: If review is no longer current or posting was already attempted.
        """
        self._owner()
        terms = ChannelDeliveryTerms.model_validate(self.review(session, request))
        if self._events(session, "channel.attempted", request):
            msg = "This posting was already attempted; inspect its receipt"
            raise HeinzelError(msg)
        proof = secrets.token_urlsafe(32)
        digest = approval_proof_hash(proof)
        now = self.crud.clock()
        record = ApprovalRecord(
            intent=self._intent(terms),
            proof_hash=digest,
            nonce=secrets.token_hex(32),
            provenance=provenance[:128],
            decision="approve",
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        with self.crud.repository.control_writer(session) as writer:
            writer.append("approval.issued", key=digest, data=record.model_dump(mode="json"))
        return proof

    def deliver(
        self,
        session: str,
        request: str,
        review: dict[str, JsonValue],
        proof: str,
        marker: ApprovalConfirmation,
    ) -> str:
        """Consume exact human posting consent before a single transport attempt; uncertainty never retries.

        Raises:
            HeinzelError: If exact confirmation or current destination and terms cannot be established.
        """
        self._owner()
        arguments: dict[str, JsonValue] = {
            "session_id": session,
            "request_id": request,
            "review": review,
            "approval_proof": proof,
        }
        if not marker.matches("heinzel", "deliver-channel-item", arguments):
            msg = "Channel delivery requires trusted human confirmation"
            raise HeinzelError(msg)
        if review != self.review(session, request):
            msg = "The reviewed channel posting changed"
            raise HeinzelError(msg)
        terms = self._terms(session, request)
        if self._events(session, "channel.attempted", request):
            return self.status(session, request)
        consumed = consume_approval(
            self.crud.repository,
            session,
            proof,
            self._intent(terms),
            lambda _events: self._intent(self._terms(session, request)),
            self.crud.clock,
            event_kind="channel.attempted",
            event_key=f"channel-attempt-{request}",
            idempotency_hash=arguments_hash(review),
            effect_data=lambda _writer, _events, _evidence: {"request_hash": arguments_hash(review)},
        )
        if consumed.disposition != "recorded":
            return self.status(session, request)
        receipt = ChannelReceipt(channel=terms.channel, status="unknown")
        try:
            binding = self._binding(terms)
            if terms.approval:
                observed = binding.call(
                    lambda service: cast("ApprovalChannel", service).post_reviewed(
                        cast("ChannelApprovalRequest", terms.approval), terms.target
                    )
                )
            else:
                observed = binding.call(
                    lambda service: cast("NotificationChannel", service).deliver_reviewed(
                        cast("ChannelNotice", terms.notice), terms.target
                    )
                )
            self._binding(terms)
            receipt = ChannelReceipt.model_validate_json(observed.model_dump_json())
            if receipt.channel != terms.channel or (
                terms.approval and receipt.status in {"queued", "delivered"} and not receipt.reference
            ):
                receipt = ChannelReceipt(channel=terms.channel, status="unknown")
        except Exception:  # noqa: BLE001 - effects may have happened; provider details may contain secrets
            receipt = ChannelReceipt(channel=terms.channel, status="unknown")
        with self.crud.repository.control_writer(session) as writer:
            writer.append(
                "channel.receipt",
                key=request,
                data={"attempt_seq": consumed.sequence, "receipt": receipt.model_dump(mode="json")},
            )
        return receipt.status

    def status(self, session: str, request: str) -> str:
        """Observe durable transport evidence without retrying a posting."""
        self._owner(daemon=True)
        self._terms(session, request)
        attempts = self._events(session, "channel.attempted", request)
        receipts = self._events(session, "channel.receipt", request)
        if not attempts:
            return "pending"
        if len(attempts) != 1 or len(receipts) != 1 or receipts[0].data.get("attempt_seq") != attempts[0].seq:
            return "unknown"
        return ChannelReceipt.model_validate(receipts[0].data.get("receipt")).status

    def _reply(self, terms: ChannelDeliveryTerms, binding: ChannelBinding) -> ChannelApprovalReply | None:
        request = terms.approval
        if request is None:
            return None
        receipts = self._events(terms.session_id, "channel.receipt", terms.request_id)
        if len(receipts) != 1 or self.status(terms.session_id, terms.request_id) not in {"queued", "delivered"}:
            return None
        receipt = ChannelReceipt.model_validate(receipts[0].data.get("receipt"))
        if receipt.reference is None or self.crud.clock() >= request.expires_at:
            return None
        replies = binding.call(
            lambda service: cast("ApprovalChannel", service).replies([cast("str", receipt.reference)])
        )
        self._binding(terms)
        posted = self._events(terms.session_id, "channel.attempted", terms.request_id)[0]
        for observed in replies[:100]:
            reply = ChannelApprovalReply.model_validate_json(observed.model_dump_json())
            if (
                reply.actor == terms.actor
                and reply.request_id == request.request_id
                and reply.arguments_sha256 == request.arguments_sha256
                and posted.ts <= reply.at <= self.crud.clock() < request.expires_at
            ):
                return reply
            with self.crud.repository.control_writer(terms.session_id) as writer:
                if not self._events(terms.session_id, "channel.rejected", terms.request_id):
                    writer.append(
                        "channel.rejected",
                        key=terms.request_id,
                        data={"channel": terms.channel, "reason": "Reply identity, digest or time did not match"},
                    )
        return None

    def apply_channel_replies(self) -> int:
        """Poll only this owner's consented requests and consume genuine exact replies once.

        Invalid external replies never issue proofs. Claimed decisions survive restart even if execution or settlement
        fails. A captured-write attempt continues to use the existing account, source, scope and revision checks.
        """
        if not feature_enabled(self.kernel.settings, "connected_channels"):
            return 0
        self._owner(daemon=True)
        count = 0
        for head in self.crud.repository.heads():
            if head.problem:
                continue
            proposals = self.crud.repository.control_read(head.session).events
            for event in proposals:
                if event.kind != "channel.proposed" or event.key is None:
                    continue
                try:
                    count += self._consume(head.session, event.key)
                except Exception:  # noqa: BLE001 - one invalid channel cannot grant authority or mask other journals
                    count += 0
        return count

    def _finish_settlement(self, terms: ChannelDeliveryTerms, binding: ChannelBinding) -> None:
        closed = self._events(terms.session_id, "channel.closed", terms.request_id)
        if len(closed) != 1 or self._events(terms.session_id, "channel.notified", terms.request_id):
            return
        outcome = closed[0].data.get("outcome")
        if outcome not in {"approved", "declined", "expired", "superseded", "uncertain"}:
            return
        receipts = self._events(terms.session_id, "channel.receipt", terms.request_id)
        if len(receipts) != 1:
            return
        receipt = ChannelReceipt.model_validate(receipts[0].data.get("receipt"))
        if receipt.reference is None or receipt.channel != terms.channel:
            return
        # Repeating settlement only closes the same posted request. It never repeats posting or a captured write.
        settled = "superseded" if outcome == "uncertain" else outcome
        binding.call(
            lambda service: cast("ApprovalChannel", service).settle(
                cast("str", receipt.reference),
                cast("Literal['approved', 'declined', 'expired', 'superseded']", settled),
            )
        )
        with self.crud.repository.control_writer(terms.session_id) as writer:
            if not self._events(terms.session_id, "channel.notified", terms.request_id):
                writer.append("channel.notified", key=terms.request_id, data={"outcome": outcome})

    def _close(self, terms: ChannelDeliveryTerms, outcome: str) -> None:
        with self.crud.repository.control_writer(terms.session_id) as writer:
            if not self._events(terms.session_id, "channel.closed", terms.request_id):
                writer.append("channel.closed", key=terms.request_id, data={"outcome": outcome})

    def _consume(self, session: str, request: str) -> int:
        with self.crud.repository.channel_writer(session):
            return self._consume_locked(session, request)

    def _consume_locked(self, session: str, request: str) -> int:  # noqa: PLR0911 - distinct terminal fail-closed states
        terms = self._terms(session, request)
        if terms.approval is None:
            return 0
        binding = self._binding(terms)
        if self._events(session, "channel.closed", request):
            self._finish_settlement(terms, binding)
            return 0
        if self._events(session, "channel.claimed", request):
            # A previous process may have died anywhere after claiming. Recovery never enters execution again.
            self._close(terms, "uncertain")
            self._finish_settlement(terms, binding)
            return 0
        if self.crud.clock() >= terms.approval.expires_at:
            self._close(terms, "expired")
            self._finish_settlement(terms, binding)
            return 0
        reply = self._reply(terms, binding)
        if reply is None:
            return 0
        marker = ApprovalConfirmation()
        write_host = self._write_host(marker)
        exact = cast("dict[str, JsonValue]", terms.write_review)
        write_request = cast("str", terms.write_request)
        # The trusted current identity stays daemon; this scoped host marker grants only this exact draft.
        with marker.using("heinzel", "execute-captured-write", exact):
            try:
                current_review = write_host.review(session, write_request)
            except HeinzelError:
                self._close(terms, "superseded")
                self._finish_settlement(terms, binding)
                return 0
            if current_review != exact:
                self._close(terms, "superseded")
                self._finish_settlement(terms, binding)
                return 0
            self._binding(terms)
            with self.crud.repository.control_writer(session) as writer:
                if (
                    self._events(session, "channel.claimed", request)
                    or self._events(session, "channel.closed", request)
                    or self.crud.clock() >= terms.approval.expires_at
                ):
                    return 0
                writer.append(
                    "channel.claimed",
                    key=request,
                    data={
                        "decision": reply.decision,
                        "arguments_sha256": reply.arguments_sha256,
                        "channel": terms.channel,
                        "actor": terms.actor,
                    },
                )
            outcome = "uncertain"
            try:
                if reply.decision == "decline":
                    write_host.discard(session, write_request)
                    outcome = "declined"
                else:
                    proof = write_host.issue(session, write_request, f"channel-human:{terms.channel}:{request}")
                    arguments: dict[str, JsonValue] = {
                        "session_id": session,
                        "request_id": write_request,
                        "review": exact,
                        "approval_proof": proof,
                    }
                    confirmation = ApprovalConfirmation()
                    with confirmation.using("heinzel", "execute-captured-write", arguments):
                        write_host.execute(session, write_request, exact, proof, confirmation)
                    outcome = "approved"
            finally:
                self._close(terms, outcome)
        self._finish_settlement(terms, binding)
        return 1
