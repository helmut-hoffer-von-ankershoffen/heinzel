"""Trusted owner publication for one bounded standing draft grant; prompts and proposals remain inert."""

import json
import secrets
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from pydantic import JsonValue

from heinzel import (
    ChannelTarget,
    Config,
    CurrentUser,
    DraftAppendService,
    DraftAppendTarget,
    HeinzelError,
    Kernel,
    OwnerNoticeChannel,
    OwnerNoticeTarget,
    ReleaseFeedBaseline,
    ReleaseFeedService,
    ReviewedLinkChannel,
    SessionEvent,
    SessionWriter,
    Settings,
)
from heinzel.kernel._approval import (
    ApprovalConfirmation,
    ApprovalEvidence,
    ApprovalIntent,
    ApprovalRecord,
    approval_proof_hash,
    arguments_hash,
    consume_approval,
)
from heinzel.kernel._features import feature_enabled
from heinzel.kernel._identity import PRIVATE_READ, PRIVATE_WRITE
from heinzel.modules.heinzel._autonomous_schedule import _fingerprint
from heinzel.modules.heinzel._draft_workflow_binding import DraftChannelBinding, DraftWorkflowBinding
from heinzel.modules.heinzel._draft_workflow_journal import DraftJournal, grant_payload, matching, target_key
from heinzel.modules.heinzel._draft_workflow_models import DraftPublicationRequest, DraftPublicationTerms
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._user_chat import UserChatService

_HUMAN_HEADS = frozenset({"cli", "web-session", "web-device"})


@dataclass(frozen=True, slots=True)
class DraftWorkflowPorts:
    """Exact source-bound ports; creating them performs no transport mutation."""

    feed: DraftWorkflowBinding[ReleaseFeedService]
    append: DraftWorkflowBinding[DraftAppendService]
    notice: DraftChannelBinding[OwnerNoticeChannel]
    share: DraftChannelBinding[ReviewedLinkChannel]


def same_document(left: DraftAppendTarget, right: DraftAppendTarget) -> bool:
    """Permit fresh conditional revisions of the same immutable account/resource/section, never another target."""
    return left.model_dump(exclude={"revision"}) == right.model_dump(exclude={"revision"})


class DraftWorkflowPublication:
    """Persist exact human publication without changing arbitrary-write approvals or minting model authority."""

    def __init__(self, crud: SessionCrud, current: CurrentUser, kernel: Kernel, chat: UserChatService) -> None:
        self.crud, self.current_user, self.kernel, self.chat = crud, current, kernel, chat
        self.journal = DraftJournal(crud)

    def owner(self, *, human: bool = False, daemon: bool = False, enabled: bool = True, read: bool = False) -> None:
        """Require an actual host-bound owner context and both current feature settings.

        Raises:
            HeinzelError: If the feature, private write scope, owner or allowed authentication is unavailable.
        """
        context = self.current_user.context()
        allowed = _HUMAN_HEADS if human else _HUMAN_HEADS | {"api-bearer"}
        if daemon:
            allowed = frozenset({"daemon"})
        if (
            context.authentication not in allowed
            or (PRIVATE_READ if read else PRIVATE_WRITE) not in context.permissions
            or context.principal != self.current_user.owner()
            or self.crud.repository.principal_id != context.principal.id
            or (enabled and not feature_enabled(self.kernel.settings, "connected_frontdoor"))
            or (
                enabled
                and not feature_enabled(Config.load(Path.home()).settings(Settings, "hz"), "connected_frontdoor")
            )
        ):
            msg = "The owner-bound draft workflow is unavailable"
            raise HeinzelError(msg)

    def ports(self, request: DraftPublicationRequest) -> DraftWorkflowPorts:
        """Resolve only explicitly implemented generic ports; no legacy or concrete-plugin fallback."""
        return DraftWorkflowPorts(
            DraftWorkflowBinding(self.kernel, "code-host", ReleaseFeedService, "release-feed"),
            DraftWorkflowBinding(self.kernel, "documents", DraftAppendService, "append-marked-draft"),
            DraftChannelBinding(self.kernel, request.notice_channel, OwnerNoticeChannel, "private-owner-notice"),
            DraftChannelBinding(
                self.kernel, request.publication_channel, ReviewedLinkChannel, "reviewed-document-link"
            ),
        )

    def proposal(self, session: str, request: str) -> DraftPublicationTerms:
        """Read and verify one immutable inert proposal; this never activates it.

        Raises:
            HeinzelError: If identity or exact proposal terms are unavailable.
        """
        events = matching(self.journal.events(session), "draft.publication-proposed", request)
        if len(events) != 1:
            msg = "The exact draft publication proposal is unavailable"
            raise HeinzelError(msg)
        terms = DraftPublicationTerms.model_validate_json(json.dumps(self.crud.read_body(session, events[0])))
        if (
            terms.owner != self.current_user.principal().id
            or terms.session_id != session
            or terms.request.request_id != request
        ):
            msg = "The draft publication identity changed"
            raise HeinzelError(msg)
        return terms

    def published(self, session: str, request: str | None = None, *, active: bool = True) -> DraftPublicationTerms:
        """Verify the actual human publication evidence and current generation, never an asserted state bit.

        Raises:
            HeinzelError: If publication is forged, superseded, revoked, expired or incomplete.
        """
        events = self.journal.events(session)
        publications = matching(events, "draft.published")
        if not publications:
            msg = "No draft workflow has been published"
            raise HeinzelError(msg)
        event = publications[-1]
        try:
            terms = DraftPublicationTerms.model_validate_json(json.dumps(self.crud.read_body(session, event)))
            issued = [
                entry
                for entry in events
                if entry.kind == "approval.issued" and entry.key == event.data.get("proof_hash")
            ]
            if len(issued) != 1:
                msg = "The draft publication evidence is invalid"
                raise HeinzelError(msg)
            record = ApprovalRecord.model_validate_json(json.dumps(issued[0].data))
            if (
                terms.owner != self.current_user.principal().id
                or terms.session_id != session
                or (request is not None and request != terms.request.request_id)
                or record.intent != self.intent(terms)
                or event.key != "publication-" + arguments_hash(terms.request.request_id)
                or event.data.get("request_hash") != arguments_hash(grant_payload(terms))
                or event.data.get("generation") != terms.generation
                or event.data.get("consumed_nonce") != record.nonce
                or event.data.get("provenance") != record.provenance
                or event.data.get("intent") != record.intent.model_dump(mode="json")
                or record.proof_hash != issued[0].key
                or record.nonce != issued[0].key
                or [entry.data.get("generation") for entry in publications] != list(range(1, len(publications) + 1))
                or not record.provenance.startswith(("chat-confirm:", "cli-publish:"))
                or not issued[0].seq < event.seq
                or not record.issued_at <= event.ts < record.expires_at
                or sum(entry.data.get("consumed_nonce") == record.nonce for entry in events) != 1
                or any(entry.kind == "approval.revoked" and entry.key == record.nonce for entry in events)
            ):
                msg = "The draft publication evidence is invalid"
                raise HeinzelError(msg)
        except (ValueError, TypeError):
            msg = "The draft publication evidence is invalid"
            raise HeinzelError(msg) from None
        if active and (
            matching(events, "draft.revoked", arguments_hash(grant_payload(terms)))
            or matching(events, "draft.paused", arguments_hash(grant_payload(terms)))
            or (terms.request.expires_at is not None and self.crud.clock() >= terms.request.expires_at)
        ):
            msg = "The draft publication is revoked, paused or expired"
            raise HeinzelError(msg)
        return terms

    def revoke(self, session: str, request: str) -> None:
        """Quiesce cooperating workers under their target fence, then prevent any new generation attempt.

        Raises:
            HeinzelError: If the owner or exact current publication is invalid.
        """
        self.owner(human=True, enabled=False)
        terms = self.published(session, request, active=False)
        with (
            self.journal.repository.draft_target_writer(session, target_key(terms), wait=True),
            self.journal.repository.main_writer(session, wait=True),
        ):
            if self.published(session, request, active=False) != terms:
                msg = "The publication generation changed"
                raise HeinzelError(msg)
            self.journal.record(
                session,
                "draft.revoked",
                arguments_hash(grant_payload(terms)),
                {"generation": terms.generation},
                "The owner revoked recurring drafts and notices. "
                "An already accepted provider request cannot be undone.",
            )

    def propose(self, session: str, request: DraftPublicationRequest) -> DraftPublicationTerms:
        """Observe a fixed initial baseline and exact supplied destinations without granting any recurring effect.

        Raises:
            HeinzelError: If an existing request changes or any selected scope cannot be verified.
        """
        self.owner(human=True)
        previous = matching(self.journal.events(session), "draft.publication-proposed", request.request_id)
        if previous:
            saved = self.proposal(session, request.request_id)
            if saved.request != request:
                msg = "This publication request already identifies different terms"
                raise HeinzelError(msg)
            self.verify_ports(saved)
            return saved
        ports = self.ports(request)
        document = ports.append.call(lambda service: service.target(request.document_reference, request.section))
        document = DraftAppendTarget.model_validate_json(document.model_dump_json())
        with ports.feed.lease(lambda service: service.open_lease(request.feed_reference)) as lease:
            baseline = ReleaseFeedBaseline.model_validate_json(
                ports.feed.call(lambda _service: lease.baseline()).model_dump_json()
            )
        notice = ports.notice.call(lambda service: service.owner_target())
        target = ports.share.call(lambda service: service.delivery_target())
        if (
            notice is None
            or target is None
            or baseline.identity.reference != request.feed_reference
            or document.reference != request.document_reference
            or document.section != request.section
        ):
            msg = "The selected workflow source or exact destination is unavailable"
            raise HeinzelError(msg)
        generations = [
            event.data.get("generation") for event in self.journal.events(session) if event.kind == "draft.published"
        ]
        terms = DraftPublicationTerms(
            owner=self.current_user.principal().id,
            session_id=session,
            request=request,
            generation=max((value for value in generations if type(value) is int), default=0) + 1,
            feed_binding=ports.feed.digest,
            feed_baseline=baseline,
            append_binding=ports.append.digest,
            document=document,
            channel_binding=ports.share.digest,
            channel_target=ChannelTarget.model_validate_json(target.model_dump_json()),
            notice_binding=ports.notice.digest,
            notice_target=OwnerNoticeTarget.model_validate_json(notice.model_dump_json()),
            model_binding=_fingerprint(self.chat),
        )
        self.journal.record(
            session,
            "draft.publication-proposed",
            request.request_id,
            grant_payload(terms),
            "A bounded draft publication is proposed. No recurring write or share is authorized.",
        )
        return terms

    def verify_ports(self, terms: DraftPublicationTerms) -> DraftWorkflowPorts:
        """Re-observe accounts, scopes and selected source code; the approved initial cursor never advances here.

        Raises:
            HeinzelError: If an immutable target, source, credential identity or model binding changed.
        """
        ports = self.ports(terms.request)
        document = ports.append.call(lambda service: service.target(terms.document.reference, terms.document.section))
        with ports.feed.lease(lambda service: service.open_lease(terms.request.feed_reference)) as lease:
            identity = ports.feed.call(lambda _service: lease.baseline()).identity
        if (
            ports.feed.digest != terms.feed_binding
            or ports.append.digest != terms.append_binding
            or ports.notice.digest != terms.notice_binding
            or ports.share.digest != terms.channel_binding
            or not same_document(document, terms.document)
            or identity != terms.feed_baseline.identity
            or ports.notice.call(lambda service: service.owner_target()) != terms.notice_target
            or ports.share.call(lambda service: service.delivery_target()) != terms.channel_target
            or _fingerprint(self.chat) != terms.model_binding
        ):
            msg = "The reviewed draft workflow source, account or exact scope changed"
            raise HeinzelError(msg)
        return ports

    @staticmethod
    def intent(terms: DraftPublicationTerms) -> ApprovalIntent:
        """Bind complete publication terms and their generation, without granting arbitrary content writes."""
        return ApprovalIntent(
            owner=terms.owner,
            audience="draft-workflow-publication",
            session=terms.session_id,
            action="publish-draft-workflow",
            resource=target_key(terms),
            arguments_hash=arguments_hash(grant_payload(terms)),
            contract_revision=0,
            grant_revision=terms.generation,
            policy_revision=1,
        )

    def issue(self, session: str, request: str, provenance: str) -> str:
        """Issue a short-lived private proof only inside the genuine owner confirmation boundary.

        Raises:
            HeinzelError: If owner context, current scope or trusted confirmation provenance is unavailable.
        """
        self.owner(human=True)
        if not provenance.startswith(("chat-confirm:", "cli-publish:")):
            msg = "Publication requires the trusted human confirmation surface"
            raise HeinzelError(msg)
        terms = self.proposal(session, request)
        self.verify_ports(terms)
        proof = secrets.token_urlsafe(32)
        digest = approval_proof_hash(proof)
        now = self.crud.clock()
        record = ApprovalRecord(
            intent=self.intent(terms),
            proof_hash=digest,
            nonce=digest,
            provenance=provenance,
            decision="approve",
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        with self.journal.repository.control_writer(session) as writer:
            writer.append("approval.issued", key=digest, data=record.model_dump(mode="json"))
        return proof

    def publish(
        self, session: str, request: str, review: dict[str, JsonValue], proof: str, confirmation: ApprovalConfirmation
    ) -> DraftPublicationTerms:
        """Consume exact human publication once under the shared target and session fences.

        Raises:
            HeinzelError: If proof, confirmation, generation or immutable reviewed scope changed.
        """
        self.owner(human=True)
        args: dict[str, JsonValue] = {
            "session_id": session,
            "request_id": request,
            "review": review,
            "approval_proof": proof,
        }
        if not confirmation.matches("heinzel", "publish-draft-workflow", args):
            msg = "Publication approval is invalid or unavailable"
            raise HeinzelError(msg)
        terms = self.proposal(session, request)
        if arguments_hash(review) != arguments_hash(grant_payload(terms)):
            msg = "The reviewed publication changed"
            raise HeinzelError(msg)

        def current(events: list[SessionEvent]) -> ApprovalIntent:
            generations = [event.data.get("generation") for event in events if event.kind == "draft.published"]
            if terms.generation != max((value for value in generations if type(value) is int), default=0) + 1:
                msg = "The publication generation changed; review new terms"
                raise HeinzelError(msg)
            return self.intent(terms)

        def effect(
            writer: SessionWriter, _events: list[SessionEvent], _evidence: ApprovalEvidence
        ) -> dict[str, JsonValue]:
            body = immutable_body(writer, "draft-publication", grant_payload(terms), control=True)
            return {
                "generation": terms.generation,
                "request_id": request,
                "request_hash": arguments_hash(grant_payload(terms)),
                **body.model_dump(mode="json"),
            }

        with (
            self.journal.repository.draft_target_writer(session, target_key(terms), wait=True),
            self.journal.repository.main_writer(session, wait=True),
        ):
            if any(
                event.data.get("request_id") == request
                for event in matching(self.journal.events(session), "draft.published")
            ):
                self.published(session, request)
            self.verify_ports(terms)
            if self.journal.pending_target_intents(session, terms):
                msg = "An uncertain document append needs explicit owner reconciliation"
                raise HeinzelError(msg)
            consume_approval(
                self.journal.repository,
                session,
                proof,
                self.intent(terms),
                current,
                self.crud.clock,
                event_kind="draft.published",
                event_key="publication-" + arguments_hash(request),
                idempotency_hash=arguments_hash(grant_payload(terms)),
                effect_data=effect,
            )
        self.journal.record(
            session,
            "draft.activated",
            request,
            {"generation": terms.generation},
            "The owner published this bounded draft workflow. Sharing still requires a separate human decision.",
        )
        return terms
