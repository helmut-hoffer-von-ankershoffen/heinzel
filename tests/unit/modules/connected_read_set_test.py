"""Inert read-set proposals never turn draft terms into read authority."""

import pytest
from pydantic import ValidationError

from heinzel import ConnectedReadReview, CurrentUser, HeinzelError, Kernel, Settings
from heinzel.modules.heinzel._connected_read_sets import ConnectedReadSets, ReadSetEntry, ReadSetProposal, ReadSetTerms
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit


@pytest.fixture
def drafts(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        session = crud.new_chat("test").session_id
        sets = ConnectedReadSets(crud, current)

        def terms(account="one", **changes):
            review = ConnectedReadReview(
                owner=current.principal().id,
                session=session,
                operation="capability-method:mail.search",
                schema_sha256="a" * 64,
                source_revision="fixture-source",
                account_identity=account,
                provider_scopes=("mail.read",),
                fixed_arguments={"query": "bounded"},
            ).model_copy(update=changes)
            return ReadSetTerms(capability="mail", method="search", binding_sha256="b" * 64, review=review)

        yield current, crud, session, sets, terms


def test_proposals_are_bounded_private_and_replay_exactly(drafts):
    _, crud, session, sets, terms = drafts
    assert sets.latest(session) is None
    first = sets.propose(session, "one", (terms(), terms("two")))
    assert first.revision == 1
    assert len({entry.handle for entry in first.entries}) == 2
    assert first.provenance == "owner-request"
    assert sets.propose(session, "one", (terms(), terms("two"))) == first
    with pytest.raises(HeinzelError, match="different read set"):
        sets.propose(session, "one", (terms("two"), terms()))
    second = sets.propose(session, "two", (terms(),))
    assert second.revision == 2
    assert sets.latest(session) == second
    events = crud.repository.control_read(session).events
    assert [event.kind for event in events if event.kind == "connected-set.proposed"] == [
        "connected-set.proposed",
        "connected-set.proposed",
    ]


def test_empty_oversized_duplicate_and_foreign_terms_are_rejected(drafts):
    current, crud, session, sets, terms = drafts
    other = crud.new_chat("other").session_id
    for entries in ((), (terms(),) * 5, (terms(), terms())):
        with pytest.raises(ValidationError):
            sets.propose(session, "invalid", entries)
    with pytest.raises(ValidationError, match="owner and session"):
        sets.propose(session, "foreign-session", (terms(session=other),))
    with pytest.raises(ValidationError, match="owner and session"):
        sets.propose(session, "foreign-owner", (terms(owner="u-" + "0" * 32),))
    assert sets.latest(session) is None
    assert current.principal().id != "u-" + "0" * 32


def test_duplicate_handle_and_mismatched_operation_are_rejected(drafts):
    current, _, session, _, terms = drafts
    entry = ReadSetEntry(handle="read-" + "a" * 32, terms=terms())
    with pytest.raises(ValidationError, match="handles must be unique"):
        ReadSetProposal(
            request_id="x", revision=1, owner=current.principal().id, session=session, entries=(entry, entry)
        )
    with pytest.raises(ValidationError, match="operation does not match"):
        terms(operation="capability-method:calendar.events")


def test_proposal_does_not_change_legacy_projection(drafts):
    current, crud, session, sets, terms = drafts
    from heinzel.modules.heinzel._connected_grants import ConnectedReadGrants

    sets.propose(session, "set", (terms(),))
    # No legacy proposal, approval, provider operation or read is appended.
    assert all(not event.kind.startswith("connected.") for event in crud.repository.control_read(session).events)
    assert not hasattr(sets, "approve")
    assert not hasattr(sets, "read")
    kernel = Kernel(Settings(), [])
    kernel.bind(CurrentUser, current)
    legacy = ConnectedReadGrants(crud, current, kernel)
    assert legacy.state(session).status == "none"
    assert legacy.tool_schema(session) is None
    with pytest.raises(HeinzelError, match="No connected read has been approved"):
        legacy.approved(session)


def test_only_authenticated_owner_can_prepare_or_project(drafts):
    current, _, session, sets, terms = drafts
    with current.local("daemon"):
        with pytest.raises(HeinzelError, match="authenticated owner"):
            sets.propose(session, "denied", (terms(),))
        with pytest.raises(HeinzelError, match="authenticated owner"):
            sets.latest(session)
    with current.using(None), pytest.raises(HeinzelError, match="authenticated"):
        sets.latest(session)
    assert sets.latest(session) is None


def test_another_principal_cannot_prepare_or_project_owner_session(drafts, tmp_path):
    current, _, session, sets, terms = drafts
    foreign = CurrentUser(tmp_path / "foreign")
    with foreign.local("cli"):
        foreign_identity = foreign.context()
    with current.using(foreign_identity):
        with pytest.raises(HeinzelError, match="authenticated owner"):
            sets.propose(session, "foreign", (terms(),))
        with pytest.raises(HeinzelError, match="authenticated owner"):
            sets.latest(session)
    assert sets.latest(session) is None


def test_terms_are_detached_and_projection_survives_repository_reopen(drafts):
    from heinzel.modules.heinzel._connected_grants import ConnectedReadGrants

    current, crud, session, sets, terms = drafts
    requested = terms()
    proposal = sets.propose(session, "stable", (requested,))
    requested.review.fixed_arguments["query"] = "changed after preparation"
    assert proposal.entries[0].terms.review.fixed_arguments == {"query": "bounded"}
    assert sets.latest(session) == proposal
    reopened_crud = SessionCrud(
        PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
    )
    assert ConnectedReadSets(reopened_crud, current).latest(session) == proposal
    kernel = Kernel(Settings(), [])
    kernel.bind(CurrentUser, current)
    legacy = ConnectedReadGrants(reopened_crud, current, kernel)
    assert legacy.state(session).status == "none"
    assert legacy.tool_schema(session) is None
    with pytest.raises(HeinzelError, match="No connected read has been approved"):
        legacy.approved(session)
    assert crud.repository.principal_id == reopened_crud.repository.principal_id
