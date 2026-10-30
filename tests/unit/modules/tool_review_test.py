"""The review facade reads a real registered fixture; it grants and invokes nothing."""

import pytest

from heinzel import CallIdentity, CurrentUser, HeinzelError, Module, Principal
from heinzel.kernel._exposure import exposed
from heinzel.modules.heinzel._host_tools import HostToolCatalog, _schema
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.heinzel._tool_argument_policy import ExactArgumentPolicy
from heinzel.modules.heinzel._tool_review import ToolReviewFacade

pytestmark = pytest.mark.unit


def test_real_owner_bound_facade_shows_registration_and_fixed_values_without_invoking(make_kernel, tmp_path):
    module = Module("fixture", "Descriptor fixture")
    calls = []

    @module.operation()
    def get(account: str, resource: str) -> str:
        """Read a local fixture resource."""
        calls.append((account, resource))
        return resource

    kernel = make_kernel(fixture=module)
    current = CurrentUser(tmp_path / "identity")
    kernel.bind(CurrentUser, current)
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=1))
        session = crud.new_chat("first").session_id
        other = crud.new_chat("other").session_id
        item = next(item for item in exposed(kernel, "mcp") if item.operation.name == "get")
        schema, binding = _schema(item, "a" * 64)
        policy = ExactArgumentPolicy("fixture:get", binding, (("account", "team-account"), ("resource", "one")))
        catalog = HostToolCatalog(
            kernel,
            current,
            crud,
            session,
            frozenset({"fixture:get"}),
            verified_code=lambda _: "a" * 64,
            argument_policies={"fixture:get": policy},
        )
        page = ToolReviewFacade(catalog).review(session)
        assert page.session_id == session
        assert len(page.items) == 1
        result = page.items[0]
        assert result.identity == "fixture:get"
        assert result.description == item.operation.summary
        assert result.input_schema == schema
        assert result.binding_sha256 == binding
        assert result.fixed_arguments == {"account": "team-account", "resource": "one"}
        assert result.policy_sha256 == policy.fingerprint()
        assert "unverified" in result.code_evidence
        assert "not verified" in result.scope_evidence
        assert not result.executable
        assert calls == []
        with pytest.raises(HeinzelError, match="different session"):
            ToolReviewFacade(catalog).review(other)
        stranger = CallIdentity(
            Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:read"})
        )
        with current.using(stranger), pytest.raises(HeinzelError, match="different owner"):
            ToolReviewFacade(catalog).review(session)
