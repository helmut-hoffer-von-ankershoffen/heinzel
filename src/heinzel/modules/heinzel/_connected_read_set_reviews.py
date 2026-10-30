"""Host-observed, inert reviews of exact connected-read sets; no issuer or read tool."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from heinzel import HeinzelError, SessionEvent, SessionWriter
from heinzel.kernel._approval import ApprovalIntent, arguments_hash
from heinzel.modules.heinzel._connected_grants import (
    ConnectedGrant,
    ConnectedGrantProposal,
    ConnectedReadGrants,
    RollingConnectedGrantProposal,
)
from heinzel.modules.heinzel._connected_read_sets import ConnectedReadSets, ReadSetEntry, ReadSetProposal
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_models import RequestId

_REQUEST = TypeAdapter(RequestId)


class ReadSetReview(BaseModel):
    """Bounded host observations, without approval, read authority or an atomic multi-provider snapshot.

    Any later grant or read must freshly revalidate each selected source and account.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: RequestId
    revision: int = Field(strict=True, ge=1)
    provenance: Literal["host-observed"] = "host-observed"
    proposal: ReadSetProposal


def read_set_intent(review: ReadSetReview) -> ApprovalIntent:
    """Canonical intent only; callers must establish current authority separately."""
    return ApprovalIntent(
        owner=review.proposal.owner,
        audience="personal-connected-read-set",
        session=review.proposal.session,
        action="approve-connected-read-set",
        resource=review.request_id,
        arguments_hash=arguments_hash(review.model_dump(mode="json")),
        contract_revision=0,
        grant_revision=review.revision,
        policy_revision=0,
    )


class ConnectedReadSetReviews:
    """Re-observe selected providers for one current inert proposal."""

    def __init__(self, sets: ConnectedReadSets, grants: ConnectedReadGrants) -> None:
        if sets.crud is not grants.crud or sets.current is not grants.current or sets.owner != grants.owner:
            msg = "The connected-read review owner changed"
            raise HeinzelError(msg)
        self.sets, self.grants = sets, grants

    def _body(self, session: str, event: SessionEvent) -> ReadSetReview:
        result = ReadSetReview.model_validate(self.sets.crud.read_body(session, event))
        if (
            result.request_id != event.key
            or result.proposal.owner != self.sets.owner
            or result.proposal.session != session
        ):
            msg = "The read-set review identity is inconsistent"
            raise HeinzelError(msg)
        return result

    @staticmethod
    def _grant(entry: ReadSetEntry) -> ConnectedGrant:
        terms = entry.terms
        common = {
            "request_id": entry.handle,
            "revision": 1,
            "capability": terms.capability,
            "method": terms.method,
            "binding_sha256": terms.binding_sha256,
            "review": terms.review,
        }
        if terms.window_policy is None:
            return ConnectedGrantProposal.model_validate(common)
        return RollingConnectedGrantProposal.model_validate(
            {**common, "kind": "rolling-window", "window_policy": terms.window_policy}
        )

    def _observe(self, session: str, proposal: ReadSetProposal) -> None:
        for entry in proposal.entries:
            grant = self._grant(entry)
            self.grants.verify_proposed_terms(session, grant)

    def prepare(self, session: str, request: str, proposal_request: str, proposal_revision: int) -> ReadSetReview:
        """Re-observe the current exact set and record an inert, versioned review.

        Raises:
            HeinzelError: If the proposal, selected provider, account or retry differs.
        """
        self.sets.latest(session)  # Authenticate and resolve the owner-bound session before acquiring a writer.
        _REQUEST.validate_python(request)
        _REQUEST.validate_python(proposal_request)
        if type(proposal_revision) is not int or proposal_revision < 1:
            msg = "The read-set proposal revision is invalid"
            raise HeinzelError(msg)
        with self.sets.crud.repository.main_writer(session):
            proposal = self.sets.latest(session)
            if proposal is None or proposal.request_id != proposal_request or proposal.revision != proposal_revision:
                msg = "The read-set proposal changed; prepare it again"
                raise HeinzelError(msg)
            self._observe(session, proposal)
            # Re-observe the whole set to detect changes between passes. This is not an atomic snapshot;
            # every later decision and read must freshly revalidate each selected source and account.
            self._observe(session, proposal)
            with self.sets.crud.repository.control_writer(session) as writer:
                current = self.sets.latest(session)
                if current is None or arguments_hash(current.model_dump(mode="json")) != arguments_hash(
                    proposal.model_dump(mode="json")
                ):
                    msg = "The read-set proposal changed; prepare it again"
                    raise HeinzelError(msg)
                events = self.sets.crud.repository.control_read(session).events
                reviews = [event for event in events if event.kind == "connected-set.reviewed"]
                previous = [event for event in reviews if event.key == request]
                if previous:
                    if len(previous) != 1:
                        msg = "This read-set review request is inconsistent"
                        raise HeinzelError(msg)
                    saved = self._body(session, previous[0])
                    if arguments_hash(saved.proposal.model_dump(mode="json")) != arguments_hash(
                        proposal.model_dump(mode="json")
                    ):
                        msg = "This request identifies a different read-set review"
                        raise HeinzelError(msg)
                    return saved
                revisions = [self._body(session, event).revision for event in reviews]
                result = ReadSetReview(request_id=request, revision=max(revisions, default=0) + 1, proposal=proposal)
                body = immutable_body(writer, "connected-set-review", result.model_dump(mode="json"), control=True)
                writer.append("connected-set.reviewed", key=request, data=body.model_dump(mode="json"))
                return result.model_copy(deep=True)

    def approval_intent(self, session: str, review_request: str, review_revision: int) -> ApprovalIntent:
        """Prepare an exact inert intent after freshly re-observing every selected source.

        This does not issue a proof, approve a set or authorize execution. An issuer must revalidate again.

        Raises:
            HeinzelError: If the review, proposal, owner, source or account changed.
        """
        self.sets.latest(session)
        _REQUEST.validate_python(review_request)
        if type(review_revision) is not int or review_revision < 1:
            msg = "The read-set review revision is invalid"
            raise HeinzelError(msg)
        with self.sets.crud.repository.main_writer(session) as writer:
            return self.review_intent_locked(session, writer, review_request, review_revision)[1]

    def review_intent_locked(
        self, session: str, writer: SessionWriter, review_request: str, review_revision: int
    ) -> tuple[ReadSetReview, ApprovalIntent]:
        """Re-observe and derive the exact intent while the owner holds the main writer.

        Raises:
            HeinzelError: If the caller lacks the writer or current reviewed terms changed.
        """
        if not self.sets.crud.repository.is_main_writer(session, writer):
            msg = "The read-set review requires its owning writer"
            raise HeinzelError(msg)
        self.sets.latest(session)
        _REQUEST.validate_python(review_request)
        if type(review_revision) is not int or review_revision < 1:
            msg = "The read-set review revision is invalid"
            raise HeinzelError(msg)
        events = self.sets.crud.repository.control_read(session).events
        reviews = [event for event in events if event.kind == "connected-set.reviewed"]
        matches = [event for event in reviews if event.key == review_request]
        if len(matches) != 1 or not reviews or matches[0] != reviews[-1]:
            msg = "The read-set review changed; review it again"
            raise HeinzelError(msg)
        review = self._body(session, matches[0])
        proposal = self.sets.latest(session)
        if (
            review.revision != review_revision
            or proposal is None
            or arguments_hash(review.proposal.model_dump(mode="json"))
            != arguments_hash(proposal.model_dump(mode="json"))
        ):
            msg = "The read-set review changed; review it again"
            raise HeinzelError(msg)
        self._observe(session, proposal)
        self._observe(session, proposal)
        return review, read_set_intent(review)
