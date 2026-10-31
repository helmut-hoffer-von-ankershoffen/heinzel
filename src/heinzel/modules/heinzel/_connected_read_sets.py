"""Inert, owner-scoped connected-read set proposals; they do not grant or execute reads."""

import secrets
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator

from heinzel import ConnectedReadReview, CurrentUser, HeinzelError, SessionEvent
from heinzel.kernel import PRIVATE_WRITE
from heinzel.kernel._approval import arguments_hash
from heinzel.modules.heinzel._connected_grants import ConnectedReadGrants, RollingWindowPolicy
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import Digest, RequestId, SessionId

_MAX_SET_ENTRIES = 4
_HEADS = frozenset({"cli", "api-bearer", "web-session", "web-device"})
_REQUEST = TypeAdapter(RequestId)
_SESSION = TypeAdapter(SessionId)


class ReadSetSelection(BaseModel):
    """The owner's requested fixed capability and arguments; it is not a source review."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    capability: str = Field(min_length=1, max_length=128, description="The configured capability to inspect.")
    method: str = Field(min_length=1, max_length=128, description="The fixed read operation to review.")
    fixed_arguments: dict[str, JsonValue] = Field(max_length=32, description="Exact arguments proposed for this read.")


class ReadSetTerms(BaseModel):
    """Unverified proposed terms, not a provider observation or a grant."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    capability: str = Field(min_length=1, max_length=128, description="The selected configured capability.")
    method: str = Field(min_length=1, max_length=128, description="The selected read operation.")
    binding_sha256: Digest = Field(description="The proposed provider binding, rechecked by the host before approval.")
    review: ConnectedReadReview = Field(
        description="Proposed source, account, scopes and read arguments; not independent evidence."
    )
    window_policy: RollingWindowPolicy | None = Field(
        default=None, description="Optional proposed rolling bounds; their presence does not authorize a schedule."
    )

    @model_validator(mode="after")
    def operation_matches(self) -> "ReadSetTerms":
        """Require matching capability and method.

        Raises:
            ValueError: If the operation does not match.
        """
        if self.review.operation != f"capability-method:{self.capability}.{self.method}":
            msg = "The proposed operation does not match its read terms"
            raise ValueError(msg)
        return self


class ReadSetEntry(BaseModel):
    """An opaque host-issued selector and its proposed terms."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    handle: str = Field(pattern=r"^read-[0-9a-f]{32}$", description="An opaque host selector for this exact entry.")
    terms: ReadSetTerms = Field(description="The exact proposed read terms, without approval.")


class ReadSetProposal(BaseModel):
    """A private proposal; its existence does not authorize any access."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: RequestId = Field(description="The stable request identifying this proposal.")
    revision: int = Field(strict=True, ge=1, description="The recorded proposal revision.")
    owner: str = Field(pattern=r"^u-[0-9a-f]{32}$", description="The authenticated owner of this private proposal.")
    session: SessionId = Field(description="The owner's agent conversation receiving these proposed sources.")
    provenance: Literal["owner-request"] = Field(default="owner-request", description="How this proposal was staged.")
    entries: tuple[ReadSetEntry, ...] = Field(
        min_length=1, max_length=_MAX_SET_ENTRIES, description="The bounded sources proposed for review together."
    )

    @model_validator(mode="after")
    def consistent_entries(self) -> "ReadSetProposal":
        """Check selectors, terms and identities.

        Raises:
            ValueError: If entries are ambiguous or belong to another owner or session.
        """
        handles: set[str] = set()
        terms: set[str] = set()
        for entry in self.entries:
            review = entry.terms.review
            if review.owner != self.owner or review.session != self.session:
                msg = "Read-set entries must belong to the owner and session"
                raise ValueError(msg)
            if entry.handle in handles:
                msg = "Read-set handles must be unique"
                raise ValueError(msg)
            handles.add(entry.handle)
            identity = arguments_hash(
                {
                    "capability": entry.terms.capability,
                    "method": entry.terms.method,
                    "binding": entry.terms.binding_sha256,
                    "account": review.account_identity,
                    "source": review.source_revision,
                    "arguments": review.fixed_arguments,
                    "window_policy": entry.terms.window_policy.model_dump(mode="json")
                    if entry.terms.window_policy
                    else None,
                }
            )
            if identity in terms:
                msg = "Read-set terms must be unique"
                raise ValueError(msg)
            terms.add(identity)
        return self


class ConnectedReadSets:
    """Persist and project inert proposals without opening a provider or issuing approval."""

    def __init__(self, crud: SessionCrud, current: CurrentUser) -> None:
        self.crud, self.current = crud, current
        self.owner = crud.repository.principal_id

    def _human(self) -> None:
        context = self.current.context()
        if (
            context.authentication not in _HEADS
            or PRIVATE_WRITE not in context.permissions
            or context.principal != self.current.owner()
            or context.principal.id != self.owner
        ):
            msg = "The read-set proposal requires its authenticated owner"
            raise HeinzelError(msg)

    def _body(self, session: str, event: SessionEvent) -> ReadSetProposal:
        proposal = ReadSetProposal.model_validate(self.crud.read_body(session, event))
        if proposal.owner != self.owner or proposal.session != session or proposal.request_id != event.key:
            msg = "The read-set proposal identity is inconsistent"
            raise HeinzelError(msg)
        return proposal

    def latest(self, session: str) -> ReadSetProposal | None:
        """Return the latest private proposal for this owner without consulting a provider."""
        self._human()
        _SESSION.validate_python(session)
        events = self.crud.repository.control_read(session).events
        proposals = [event for event in events if event.kind == "connected-set.proposed"]
        return self._body(session, proposals[-1]) if proposals else None

    def propose_selected(
        self, grants: ConnectedReadGrants, session: str, request: str, selections: tuple[ReadSetSelection, ...]
    ) -> ReadSetProposal:
        """Observe an owner's bounded selected reads without granting or executing them.

        Raises:
            HeinzelError: If owner, selection or selected source cannot be verified.
        """
        self._human()
        if (
            grants.crud is not self.crud
            or grants.current is not self.current
            or not 1 <= len(selections) <= _MAX_SET_ENTRIES
        ):
            msg = "The read-set selection is invalid"
            raise HeinzelError(msg)
        terms: list[ReadSetTerms] = []
        for selection in selections:
            binding, review = grants.observe_set_candidate(
                session, selection.capability, selection.method, selection.fixed_arguments
            )
            terms.append(
                ReadSetTerms(
                    capability=selection.capability, method=selection.method, binding_sha256=binding, review=review
                )
            )
        return self.propose(session, request, tuple(terms))

    def propose(self, session: str, request: str, terms: tuple[ReadSetTerms, ...]) -> ReadSetProposal:
        """Persist a draft; exact retries return its original selectors.

        Raises:
            HeinzelError: If the request is inconsistent or identifies different terms.
        """
        self._human()
        _SESSION.validate_python(session)
        _REQUEST.validate_python(request)
        self.crud.repository.control_read(session)
        with self.crud.repository.main_writer(session), self.crud.repository.control_writer(session) as writer:
            events = self.crud.repository.control_read(session).events
            proposals = [event for event in events if event.kind == "connected-set.proposed"]
            previous = [event for event in proposals if event.key == request]
            if previous:
                if len(previous) != 1:
                    msg = "This read-set request is inconsistent"
                    raise HeinzelError(msg)
                saved = self._body(session, previous[0])
                if arguments_hash([entry.terms.model_dump(mode="json") for entry in saved.entries]) != arguments_hash(
                    [entry.model_dump(mode="json") for entry in terms]
                ):
                    msg = "This request identifies a different read set"
                    raise HeinzelError(msg)
                return saved
            revisions = [self._body(session, e).revision for e in proposals]
            proposal = ReadSetProposal(
                request_id=request,
                revision=max(revisions, default=0) + 1,
                owner=self.owner,
                session=session,
                entries=tuple(
                    ReadSetEntry(handle=f"read-{secrets.token_hex(16)}", terms=entry.model_copy(deep=True))
                    for entry in terms
                ),
            )
            body = immutable_body(writer, "connected-read-set", proposal.model_dump(mode="json"), control=True)
            writer.append("connected-set.proposed", key=request, data=body.model_dump(mode="json"))
            return proposal
