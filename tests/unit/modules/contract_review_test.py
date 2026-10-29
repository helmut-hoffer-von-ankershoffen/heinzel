"""Exact inert contract acceptance uses the trusted issuer and owner journal."""

import pytest

from heinzel import CallIdentity, CurrentUser, HeinzelError, Principal
from heinzel.modules.heinzel._contract_review import ContractReview
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit


def test_acceptance_is_exact_idempotent_owner_scoped_and_never_activates(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        session = crud.new_chat("contract").session_id
        proposal = crud.propose_scheduled_agent(session, "one", "Exact terms", None, 30)
        review = ContractReview(crud)
        first = review.issue(session, "one", "chat-confirm:test:one")
        competing = review.issue(session, "one", "chat-confirm:test:two")
        assert review.accept(session, "one", first) == proposal
        assert review.accept(session, "one", first) == proposal
        assert review.applied(session, "one")
        with pytest.raises(HeinzelError):
            review.accept(session, "one", competing)
        assert crud.snapshot(session).schedule is None
        events = crud.repository.control_read(session).events
        accepted = [item for item in events if item.kind == "agent.contract-accepted"]
        assert len(accepted) == 1
        assert accepted[0].data["provenance"] == "chat-confirm:test:one"
        assert accepted[0].data["intent"]["owner"] == current.principal().id
        amended = crud.amend_scheduled_agent(session, "two", 1, "Changed terms", None, 30)
        assert amended.revision == 2
        assert not review.applied(session, "two")
        with pytest.raises(HeinzelError):
            review.issue(session, "one", "chat-confirm:test:three")
        stranger = CallIdentity(
            Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:read"})
        )
        with current.using(stranger), pytest.raises(HeinzelError):
            review.applied(session, "one")
