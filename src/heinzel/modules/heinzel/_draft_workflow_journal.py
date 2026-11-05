"""Private workflow observations and owner-wide append fences; no provider operation or approval issuer."""

import hashlib
from collections.abc import Mapping

from pydantic import JsonValue

from heinzel import HeinzelError, SessionEvent, SessionWriter
from heinzel.kernel._approval import arguments_hash
from heinzel.modules.heinzel._draft_workflow_models import DraftPublicationTerms
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionMessage
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository


def target_key(terms: DraftPublicationTerms) -> str:
    """Scope the fence to the physical owner/account/document, independent of grant or adapter generations."""
    return arguments_hash([terms.owner, terms.document.account, terms.document.resource_id])


def grant_payload(terms: DraftPublicationTerms) -> dict[str, JsonValue]:
    """The complete immutable publication terms; authority still comes from the trusted publication record."""
    return terms.model_dump(mode="json")


def matching(events: list[SessionEvent], kind: str, key: str | None = None) -> list[SessionEvent]:
    """Select exact metadata without treating matching text as authority."""
    return [event for event in events if event.kind == kind and (key is None or event.key == key)]


class DraftJournal:
    """Use existing private body/journal machinery for every transition and its same-chat explanation."""

    def __init__(self, crud: SessionCrud) -> None:
        """Require the actual owner-private repository, never a project or model-provided persistence seam.

        Raises:
            HeinzelError: If this is not the authenticated personal repository.
        """
        if not isinstance(crud.repository, PersonalSessionRepository):
            msg = "Draft workflows require the owner-private personal repository"
            raise HeinzelError(msg)
        self.crud = crud
        self.repository = crud.repository

    def events(self, session: str) -> list[SessionEvent]:
        """Read only the current authenticated owner's verified session spine."""
        return self.crud.repository.control_read(session).events

    def record(self, session: str, kind: str, key: str, value: JsonValue, text: str) -> SessionEvent:
        """Fsync a private transition and an explicit host message without inventing an assistant reply.

        Raises:
            HeinzelError: If an exact transition key is replayed with different immutable terms.
        """
        with self.crud.repository.control_writer(session) as writer:
            prior = matching(self.events(session), kind, key)
            if prior:
                if len(prior) != 1 or arguments_hash(self.crud.read_body(session, prior[0])) != arguments_hash(value):
                    msg = "The draft workflow transition changed"
                    raise HeinzelError(msg)
                event = prior[0]
            else:
                body = immutable_body(writer, "draft-state", value, control=True)
                event = writer.append(kind, key=key, data=body.model_dump(mode="json"))
            message_id = "workflow-" + hashlib.sha256(f"{kind}\0{key}".encode()).hexdigest()
            if not matching(self.events(session), "draft.workflow-message", message_id):
                message = SessionMessage(
                    id=message_id,
                    seq=1,
                    role="system",
                    origin=self.crud.repository.authentication,
                    at=event.ts,
                    text=text,
                )
                body = immutable_body(writer, "draft-message", message.model_dump(mode="json"), control=True)
                writer.append(
                    "draft.workflow-message",
                    key=message_id,
                    data={"message_id": message_id, **body.model_dump(mode="json")},
                )
            return event

    def pending_target_intents(self, session: str, terms: DraftPublicationTerms) -> list[SessionEvent]:
        """Unsettled appends fence all cooperating generations writing this physical document."""
        events = self.repository.draft_target_read(session, target_key(terms)).events
        settled = {event.key for event in events if event.kind in {"draft.target-settled", "draft.target-set-aside"}}
        return [event for event in events if event.kind == "draft.target-intent" and event.key not in settled]

    def begin_append(
        self, session: str, terms: DraftPublicationTerms, key: str, writer: SessionWriter, data: Mapping[str, JsonValue]
    ) -> None:
        """Commit the shared target fence before the session attempt, and both before any append.

        Raises:
            HeinzelError: If any earlier append outcome is unresolved, including another helper's.
        """
        if not self.repository.is_draft_target_writer(session, target_key(terms), writer):
            msg = "The append does not hold its owner target fence"
            raise HeinzelError(msg)
        if self.pending_target_intents(session, terms):
            msg = "An uncertain document append needs explicit owner reconciliation"
            raise HeinzelError(msg)
        writer.append(
            "draft.target-intent",
            key=key,
            data={"session": session, "grant_sha256": arguments_hash(grant_payload(terms)), **data},
        )

    @staticmethod
    def settle_append(key: str, writer: SessionWriter) -> None:
        """Close only an already acknowledged host attempt; never infer an acknowledgement from document reads."""
        writer.append("draft.target-settled", key=key, data={})
