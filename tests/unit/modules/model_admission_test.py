"""Admission never sends a model request or mistakes discovery for a hard budget."""

from decimal import Decimal

import pytest

from heinzel import AccessStatus, CurrentUser, HeinzelError
from heinzel.modules.heinzel._model_admission import require_connected_model
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit


class OfflineAccess:
    def __init__(self, status):
        self.response = status
        self.calls = 0

    def status(self):
        self.calls += 1
        return self.response

    def test(self):
        pytest.fail("Admission must never run a paid live model test")


@pytest.mark.parametrize(
    ("selected", "status", "model", "budget", "error", "status_calls"),
    [
        (None, True, "openai:model", Decimal(1), "does not match", 0),
        ("other", True, "openai:model", Decimal(1), "does not match", 0),
        ("pydantic-ai", False, "openai:model", Decimal(1), "credential is unavailable", 1),
        ("pydantic-ai", True, "openai:other", Decimal(1), "model is unavailable", 1),
        ("pydantic-ai", True, "openai:model", None, "explicit finite spending limit", 1),
        ("pydantic-ai", True, "openai:model", Decimal("NaN"), "explicit finite spending limit", 1),
        ("pydantic-ai", True, "openai:model", Decimal(1), "pre-execution spending ceiling", 1),
    ],
)
def test_preflight_rejects_unverified_access_and_spend(  # noqa: PLR0913, PLR0917 - parametrized cases
    tmp_path, selected, status, model, budget, error, status_calls
):
    current = CurrentUser(tmp_path / "state" / "identity")
    with current.local("cli"):
        repository = PersonalSessionRepository(current)
        crud = SessionCrud(repository, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("create").session_id
        access = OfflineAccess(
            AccessStatus(title="test", summary="test", installed=True, signed_in=status, models=["openai:model"])
        )
        with pytest.raises(HeinzelError, match=error):
            require_connected_model(
                repository,
                access,
                session_id=session,
                owner=current.principal().id,
                selected_access=selected,
                approved_access="pydantic-ai",
                approved_model=model,
                budget_usd=budget,
            )
        assert access.calls == status_calls


def test_preflight_rejects_foreign_owner_before_access_or_network(tmp_path):
    current = CurrentUser(tmp_path / "state" / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("create").session_id
        access = OfflineAccess(AccessStatus(title="test", summary="test", installed=True, signed_in=True))
        with pytest.raises(HeinzelError, match="different authenticated owner"):
            require_connected_model(
                repo,
                access,
                session_id=session,
                owner="u-" + "f" * 32,
                selected_access="pydantic-ai",
                approved_access="pydantic-ai",
                approved_model="openai:model",
                budget_usd=Decimal(1),
            )
        assert access.calls == 0


def test_connected_turn_refusal_precedes_start_and_model_calls(tmp_path):
    from heinzel.modules.heinzel._interactive_turn import InteractiveTurn
    from heinzel.modules.heinzel._session_models import MessageRequest

    current = CurrentUser(tmp_path / "state" / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("create").session_id
        crud.accept(session, MessageRequest(request_id="one", text="hello"))
        access = OfflineAccess(
            AccessStatus(title="test", summary="test", installed=True, signed_in=True, models=["openai:model"])
        )
        with pytest.raises(HeinzelError, match="pre-execution spending ceiling"):
            InteractiveTurn(crud).run_connected(
                session,
                access,
                selected_access="pydantic-ai",
                approved_access="pydantic-ai",
                approved_model="openai:model",
                budget_usd=Decimal(1),
            )
        assert all(event.kind != "interactive.started" for event in repo.control_read(session).events)
