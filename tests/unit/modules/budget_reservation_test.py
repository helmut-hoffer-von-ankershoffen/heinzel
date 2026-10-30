"""Owner-wide reservations count worst-case spend across restarts, including uncertain attempts."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from heinzel import CallIdentity, CurrentUser, HeinzelError, Principal
from heinzel.modules.heinzel._budget_reservation import PriceEnvelope, reserve_model_spend
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit


def envelope():
    return PriceEnvelope(
        model="example:configured",
        price_revision="trusted-policy-v1",
        max_input_tokens=10_000,
        max_output_tokens=1000,
        max_requests=2,
        input_usd_per_million=Decimal(2),
        output_usd_per_million=Decimal(5),
    )


def reserve(  # noqa: PLR0913 - test helper exposes all three independent caps
    repo, session, request, *, owner_cap=Decimal(1), session_cap=Decimal(1), message_cap=Decimal(1)
):
    return reserve_model_spend(
        repo, session, request, envelope(), owner_cap=owner_cap, session_cap=session_cap, message_cap=message_cap
    )


def test_reservation_survives_restart_and_never_authorizes_replay(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        one = crud.new_chat("one").session_id
        two = crud.new_chat("two").session_id
        first = reserve(repo, one, "request")
        assert first.disposition == "reserved"
        assert first.amount == Decimal("0.05")
        reopened = PersonalSessionRepository(current)
        assert reserve(reopened, one, "request").disposition == "existing"
        assert reserve(reopened, two, "request").owner_reserved == Decimal("0.10")
        with pytest.raises(HeinzelError, match="budget cap"):
            reserve(reopened, one, "next", session_cap=Decimal("0.09"))
        with pytest.raises(HeinzelError, match="budget cap"):
            reserve(reopened, two, "next", owner_cap=Decimal("0.14"))
        with pytest.raises(HeinzelError, match="budget cap"):
            reserve(reopened, one, "next", message_cap=Decimal("0.049"))
        changed = envelope().model_copy(update={"max_requests": 1})
        with pytest.raises(HeinzelError, match="different model bounds"):
            reserve_model_spend(
                reopened,
                one,
                "request",
                changed,
                message_cap=Decimal(1),
                session_cap=Decimal(1),
                owner_cap=Decimal(1),
            )


def test_concurrent_cross_session_reservations_cannot_exceed_owner_cap(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        sessions = [crud.new_chat(f"agent-{n}").session_id for n in range(2)]

    def attempt(session):
        with current.local("cli"):
            try:
                return reserve(
                    PersonalSessionRepository(current), session, "request", owner_cap=Decimal("0.05")
                ).disposition
            except HeinzelError:
                return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, sessions)) == ["denied", "reserved"]


def test_concurrent_requests_cannot_exceed_one_turn_token_cap(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        session = (
            SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=1)).new_chat("one").session_id
        )

    def attempt(request):
        with current.local("cli"):
            try:
                return reserve_model_spend(
                    PersonalSessionRepository(current),
                    session,
                    request,
                    envelope(),
                    turn_id="same-turn",
                    max_turn_tokens=22_000,
                    message_cap=Decimal(2),
                    session_cap=Decimal(2),
                    owner_cap=Decimal(2),
                ).disposition
            except HeinzelError:
                return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, ["request-one", "request-two"])) == ["denied", "reserved"]


def test_owner_cannot_reserve_against_another_owners_session(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("one").session_id
    stranger = CallIdentity(Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:read"}))
    with current.using(stranger), pytest.raises(HeinzelError, match="No such personal"):
        reserve(repo, session, "request")


def test_corrupt_owner_budget_ledger_fails_closed(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("one").session_id
        with repo.budget_writer(session) as writer:
            writer.append("model.reserved", key="bogus", data={"amount": "0"})
        with pytest.raises(HeinzelError, match="invalid reservation"):
            reserve(repo, session, "request")


def test_initialize_refuses_known_previous_model_work_and_is_owner_scoped(tmp_path):
    from heinzel.modules.heinzel._budget_reservation import initialize_budget

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        one = crud.new_chat("one").session_id
        two = crud.new_chat("two").session_id
        with repo.control_writer(one) as writer:
            writer.append("interactive.started", key="old", data={})
        with pytest.raises(HeinzelError, match="Prior model work"):
            initialize_budget(repo, two)
        assert repo.budget_read(two).events == []


def test_initialized_ledger_counts_multiple_requests_against_same_message_cap(tmp_path):
    from heinzel.modules.heinzel._budget_reservation import initialize_budget

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("one").session_id
        initialize_budget(repo, session)
        initialize_budget(repo, session)
        options = {
            "turn_id": "turn",
            "require_initialized": True,
            "message_cap": Decimal("0.09"),
            "session_cap": Decimal(1),
            "owner_cap": Decimal(1),
        }
        assert reserve_model_spend(repo, session, "call-one", envelope(), **options).disposition == "reserved"
        with pytest.raises(HeinzelError, match="budget cap"):
            reserve_model_spend(repo, session, "call-two", envelope(), **options)
