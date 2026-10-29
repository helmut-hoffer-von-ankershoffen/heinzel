"""Model policy proposals remain inert and require the trusted exact confirmation boundary."""

import pytest

from heinzel import CallIdentity, CurrentUser, HeinzelError, Principal
from heinzel.kernel._approval import ApprovalConfirmation
from heinzel.modules.heinzel._model_grants import ModelGrant, ModelGrants
from heinzel.modules.heinzel._model_policy_operations import ModelPolicyService
from heinzel.modules.heinzel._priced_model import MODEL
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit


def test_exact_owner_confirmation_and_stale_replay(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=1)
        )
        session = crud.new_chat("create").session_id
        grant = ModelGrant(
            request_id="policy",
            revision=1,
            access="pydantic-ai",
            model=MODEL,
            requests=1,
            total_tokens=100,
            output_tokens=10,
            timeout_seconds=10,
            message_cap_usd="1",
            session_cap_usd="2",
            owner_cap_usd="3",
        )
        payload = grant.model_dump(mode="json")
        service = ModelPolicyService(crud)
        confirmation = ApprovalConfirmation()
        with pytest.raises(HeinzelError):
            service.propose(session, "subscription", payload)
        with pytest.raises(HeinzelError, match="catalog review"):
            service.propose(session, "api-priced", {**payload, "allowed_tools": {"plugin:read": "a" * 64}})
        assert service.propose(session, "api-priced", payload).proposal == payload
        assert service.propose(session, "api-priced", payload).status == "proposed"
        with pytest.raises(HeinzelError):
            ModelGrants(crud).approved(session)
        proof = ModelGrants(crud).issue(session, "policy", "trusted-test-confirmation")
        with pytest.raises(HeinzelError):
            service.confirm(confirmation, session, "policy", proof)
        with (
            confirmation.using(
                "heinzel",
                "approve-model-grant",
                {"session_id": session, "request_id": "other", "approval_proof": proof},
            ),
            pytest.raises(HeinzelError),
        ):
            service.confirm(confirmation, session, "policy", proof)
        args = {"session_id": session, "request_id": "policy", "approval_proof": proof}
        with confirmation.using("heinzel", "approve-model-grant", args):
            assert service.confirm(confirmation, session, "policy", proof).status == "approved"
            with pytest.raises(HeinzelError):
                service.confirm(confirmation, session, "policy", proof)
        assert ModelGrants(crud).approved(session) == grant
        with pytest.raises(HeinzelError):
            service.review(session, "policy")
        stranger = CallIdentity(
            Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:read"})
        )
        with current.using(stranger), pytest.raises(HeinzelError):
            service.review(session, "policy")
