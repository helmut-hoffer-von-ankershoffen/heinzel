"""Journaled external effects for the separately granted draft workflow; no effect is automatically replayed."""

import hashlib
import json
from typing import TYPE_CHECKING

from pydantic import SecretStr

from heinzel import (
    ChannelNotice,
    ChannelReceipt,
    DraftAppendAttempt,
    DraftAppendReceipt,
    DraftAppendReview,
    HeinzelError,
    SessionWriter,
)
from heinzel.kernel._approval import arguments_hash
from heinzel.modules.heinzel._draft_workflow_journal import grant_payload, matching, target_key
from heinzel.modules.heinzel._draft_workflow_models import DraftPublicationTerms, DraftRelease
from heinzel.modules.heinzel._draft_workflow_publication import (
    DraftWorkflowPorts,
    DraftWorkflowPublication,
    same_document,
)
from heinzel.modules.heinzel._turn_activity import interactive_turn_id

if TYPE_CHECKING:
    from pydantic import JsonValue


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise HeinzelError(message)


def verified(host: DraftWorkflowPublication, terms: DraftPublicationTerms) -> DraftWorkflowPorts:
    """Recheck the actual current owner/generation and every bound source immediately before an effect.

    Raises:
        HeinzelError: If the real context, grant, source or exact scopes are no longer current.
    """
    host.owner(daemon=host.current_user.context().authentication == "daemon")
    if arguments_hash(grant_payload(host.published(terms.session_id))) != arguments_hash(grant_payload(terms)):
        msg = "The published draft generation changed"
        raise HeinzelError(msg)
    return host.verify_ports(terms)


def _fence(host: DraftWorkflowPublication, terms: DraftPublicationTerms, writer: SessionWriter) -> None:
    if not host.journal.repository.is_draft_target_writer(terms.session_id, target_key(terms), writer):
        msg = "The draft effect does not hold its owner target fence"
        raise HeinzelError(msg)


def marked_text(terms: DraftPublicationTerms, key: str, poem: str) -> str:
    """The host owns the marker, attribution, opaque anchor and fixed source URL; the model supplies literal text.

    Raises:
        HeinzelError: If the complete marked UTF-8 content does not fit the owner's selected bound.
    """
    text = (
        "\nDRAFT — awaiting your review\n"
        f"Heinzel: {terms.request.name}\nDraft: {key}\nSource: {terms.request.feed_reference}\n\n{poem}\n"
    )
    if not poem.strip() or len(text.encode("utf-8")) > terms.request.max_draft_bytes:
        msg = "The generated draft exceeds the published UTF-8 content bound"
        raise HeinzelError(msg)
    return text


def append_draft(  # noqa: PLR0913, PLR0917 - independent captured grant, release, poem and actual target fence
    host: DraftWorkflowPublication,
    terms: DraftPublicationTerms,
    key: str,
    release_sha256: str,
    poem: str,
    writer: SessionWriter,
) -> None:
    """Attempt one conditional marked append after a durable target fence and immutable session intent.

    Raises:
        HeinzelError: If scope is unavailable or the outcome cannot be completely acknowledged and quiesced.
    """
    session = terms.session_id
    host.owner(daemon=True)
    _fence(host, terms, writer)
    if matching(host.journal.events(session), "draft.append-attempted", key):
        msg = "This release append was already attempted and cannot be repeated"
        raise HeinzelError(msg)
    observed = matching(host.journal.events(session), "draft.release-observed", key)
    _expect(len(observed) == 1, "This release has no immutable host observation")
    value = host.crud.read_body(session, observed[0])
    if not isinstance(value, dict):
        msg = "The observed release record is invalid"
        raise HeinzelError(msg)
    release = DraftRelease.model_validate_json(json.dumps(value.get("release")))
    _expect(
        release.key == key
        and release.release_sha256 == release_sha256
        and value.get("grant_sha256") == arguments_hash(grant_payload(terms))
        and value.get("generation") == terms.generation,
        "The observed release does not belong to this grant",
    )
    replies = matching(host.journal.events(session), "message.replied", interactive_turn_id("poem-" + key))
    _expect(
        len(replies) == 1 and host.crud.read_message(session, replies[0]).text == poem,
        "The poem is not this release's recorded bounded model answer",
    )
    text = marked_text(terms, key, poem)
    with host.journal.repository.main_writer(session):
        ports = verified(host, terms)
        target = ports.append.call(lambda service: service.target(terms.document.reference, terms.document.section))
        if not same_document(target, terms.document):
            msg = "The published document scope changed"
            raise HeinzelError(msg)
        attempt = DraftAppendAttempt(
            owner=terms.owner,
            session=session,
            grant_sha256=arguments_hash(grant_payload(terms)),
            draft_key=key,
            release_sha256=release_sha256,
            target=target,
            text=SecretStr(text),
        )
        payload: dict[str, JsonValue] = {**attempt.model_dump(mode="json"), "text": text}
        digest = arguments_hash(payload)
        host.journal.begin_append(
            session, terms, key, writer, {"attempt_sha256": digest, "generation": terms.generation}
        )
        host.journal.record(
            session,
            "draft.append-attempted",
            key,
            {"attempt_sha256": digest, "attempt": payload},
            "A conditional marked-draft append was attempted. An incomplete outcome is never retried automatically.",
        )
        try:
            verified(host, terms)
            with ports.append.lease(lambda service: service.open_lease(attempt)) as lease:
                review = ports.append.call(lambda _service: lease.review())
                expected = DraftAppendReview(
                    draft_key=key,
                    target=target,
                    text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    append_only=True,
                )
                _expect(review == expected, "The captured append terms changed")
                verified(host, terms)
                receipt = ports.append.call(lambda _service: lease.append())
                receipt = DraftAppendReceipt.model_validate_json(receipt.model_dump_json())
                _expect(
                    receipt.draft_key == key and receipt.reference == terms.document.reference,
                    "The provider acknowledgement does not identify this append",
                )
            after = ports.append.call(lambda service: service.target(terms.document.reference, terms.document.section))
            _expect(same_document(after, terms.document), "The document account or source changed after append")
        except Exception:  # noqa: BLE001 - any failure after the durable intent may follow a real remote mutation
            host.journal.record(
                session,
                "draft.append-uncertain",
                key,
                {"attempt_sha256": digest},
                "This draft append has an uncertain outcome. No automatic retry or later read can acknowledge it.",
            )
            msg = "The draft append outcome is uncertain; inspect and reconcile it explicitly"
            raise HeinzelError(msg) from None
        host.journal.record(
            session,
            "draft.append-acknowledged",
            key,
            {"attempt_sha256": digest, "receipt": receipt.model_dump(mode="json")},
            "The provider acknowledged the conditional draft append. "
            "Edit the document, then review its link before sharing.",
        )
        host.journal.settle_append(key, writer)


def post_owner_notice(
    host: DraftWorkflowPublication,
    terms: DraftPublicationTerms,
    key: str,
    writer: SessionWriter,
    *,
    confirmation: bool = False,
) -> None:
    """Use only the fixed private owner transport once; a notice failure never repeats the append.

    Raises:
        HeinzelError: If scope is unavailable or a notice outcome is unknown.
    """
    session = terms.session_id
    _fence(host, terms, writer)
    phase = "confirmation" if confirmation else "review"
    kind = "draft." + phase + "-notice"
    if matching(host.journal.events(session), kind + "-attempted", key):
        return
    required = "draft.share-acknowledged" if confirmation else "draft.append-acknowledged"
    if not matching(host.journal.events(session), required, key):
        msg = "A notice needs the preceding effect's actual acknowledgement"
        raise HeinzelError(msg)
    ports = verified(host, terms)
    notice = ChannelNotice(
        key=arguments_hash(["draft-owner-notice", phase, key]),
        helper=terms.request.name,
        title="Document link shared" if confirmation else "A release draft is ready for review",
        body=(
            "The reviewed rolling-document link was accepted by its chosen channel."
            if confirmation
            else "Edit the marked draft in your rolling document, then open this chat to review sharing its link."
        ),
        route="/heinzel?chat=" + session,
    )
    host.journal.record(
        session,
        kind + "-attempted",
        key,
        {"notice": notice.model_dump(mode="json"), "target": terms.notice_target.model_dump(mode="json")},
        "A private owner confirmation notice was attempted."
        if confirmation
        else "A private owner review notice was attempted.",
    )
    try:
        verified(host, terms)
        receipt = ports.notice.call(lambda service: service.deliver_owner_reviewed(notice, terms.notice_target))
        receipt = ChannelReceipt.model_validate_json(receipt.model_dump_json())
        _expect(
            receipt.channel == terms.request.notice_channel
            and receipt.status != "unknown"
            and ports.notice.call(lambda service: service.owner_target()) == terms.notice_target,
            "The private notice acknowledgement is unavailable",
        )
    except Exception:  # noqa: BLE001 - posting may have succeeded before a transport or scope failure
        host.journal.record(
            session,
            kind + "-uncertain",
            key,
            {"status": "unknown"},
            "The private notice outcome is uncertain. It is not automatically reposted; "
            "the document append is not repeated.",
        )
        msg = "The private owner notice outcome is uncertain"
        raise HeinzelError(msg) from None
    host.journal.record(
        session,
        kind + "-acknowledged",
        key,
        {"status": receipt.status, "reference": receipt.reference},
        "The private owner notice transport reported "
        + receipt.status
        + ". This is separate from the document receipt.",
    )
