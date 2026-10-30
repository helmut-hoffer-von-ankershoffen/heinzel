"""Internal connected grants require exact owner, trusted proof and current source."""

import asyncio

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from heinzel import (
    CallIdentity,
    Config,
    ConnectedReadLease,
    ConnectedReadReview,
    ConnectedReadService,
    CurrentUser,
    HeinzelError,
    Kernel,
    Module,
    ModuleRecord,
    Principal,
    Settings,
)
from heinzel.kernel import Origin
from heinzel.kernel._approval import ApprovalConfirmation, arguments_hash
from heinzel.modules.heinzel._capability_review import describe_capability_read
from heinzel.modules.heinzel._connected_grants import ConnectedReadGrants
from heinzel.modules.heinzel._engine import TurnLimits
from heinzel.modules.heinzel._interactive_turn import InteractiveTurn
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import MessageRequest, SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit


def _model(expected, summary):
    calls = 0

    def respond(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse([ToolCallPart("use_tool", {"name": "connected_read", "arguments": {}})])
        assert any(
            isinstance(part, ToolReturnPart) and expected in str(part.content).lower()
            for message in messages
            for part in message.parts
        )
        return ModelResponse([ToolCallPart(info.output_tools[0].name, {"report": summary, "summary": summary})])

    return FunctionModel(respond)


@pytest.fixture
def connected(tmp_path, monkeypatch):
    current = CurrentUser(tmp_path / "identity")
    state = {"account": "account-a", "source": "source-v1", "scopes": ("calendar", "drive"), "reads": 0, "opens": 0}
    holder = {}

    class Lease(ConnectedReadLease):
        def __init__(self, session, operation, fixed):
            self.session, self.operation, self.fixed = session, operation, fixed

        def review(self):
            descriptor = describe_capability_read(holder["kernel"], "calendar", "events")
            return ConnectedReadReview(
                owner=current.principal().id,
                session=self.session,
                operation=self.operation,
                schema_sha256=arguments_hash(descriptor.schema),
                source_revision=state["source"],
                account_identity=state["account"],
                provider_scopes=state["scopes"],
                fixed_arguments={**self.fixed, **({"count": True} if state.get("coerce") else {})},
            )

        def read(self, arguments):
            assert arguments == {}
            state["reads"] += 1
            return state.get("result", {"events": ["fictional"]})

        def close(self):
            pass

    class Provider(ConnectedReadService):
        def open_lease(self, session, operation, fixed_arguments):
            state["opens"] += 1
            return Lease(session, operation, dict(fixed_arguments))

    class Calendar:
        def events(self, window):
            return []

        def free_busy(self, people, window):
            return []

    module = Module("fixture", "fixture")
    module.service(Provider)
    module.provides("calendar", modes={"read"}, name="fixture", hosts=["fixture.invalid"])(Calendar)
    origin = Origin(distribution="fixture", version="1", builtin=False)
    record = ModuleRecord(qualname="fixture", module=module, origin=origin)
    kernel = Kernel(Settings(), [record], config=Config(files=(), data={"capabilities": {"calendar": "fixture"}}))
    kernel.bind(CurrentUser, current)
    holder["kernel"] = kernel
    state["config"] = kernel.config
    monkeypatch.setattr(Config, "load", classmethod(lambda _cls, _cwd=None: state["config"]))

    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        session = crud.new_chat("test").session_id
        grants = ConnectedReadGrants(crud, current, kernel)
        fixed = {"window": {"since": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00Z"}}
        yield current, crud, session, grants, fixed, state


def test_exact_approval_current_source_and_revoke(connected):  # noqa: PLR0915
    current, crud, session, grants, fixed, state = connected
    with pytest.raises(HeinzelError):
        grants.propose(session, "bad", "calendar", "events", {"window": {**fixed["window"], "extra": "ignored"}})
    with pytest.raises(HeinzelError):
        grants.propose(session, "bad", "calendar", "create_event", fixed)
    assert grants.state(session).status == "none"
    assert any(o.capability == "calendar" and o.method == "events" for o in grants.options(session))
    assert state["opens"] == 0
    proposal = grants.propose(session, "req-one", "calendar", "events", fixed)
    assert grants.state(session).status == "proposed"
    with crud.repository.main_writer(session) as writer, pytest.raises(HeinzelError):
        grants.read(session, writer)
    assert state["reads"] == 0
    crud.accept(session, MessageRequest(request_id="before", text="Please read."))
    asyncio.run(
        InteractiveTurn(crud).run(
            session, "before", _model("unavailable", "Not available."), limits=TurnLimits(), connected=grants
        )
    )
    assert state["reads"] == 0
    proof = grants.issue(session, "req-one", "human-confirmation")
    competing = grants.issue(session, "req-one", "human-confirmation")
    confirmation = ApprovalConfirmation()
    review = proposal.model_dump(mode="json")
    args = {"session_id": session, "request_id": "req-one", "review": review, "approval_proof": proof}
    with pytest.raises(HeinzelError):
        grants.approve(session, "req-one", review, proof, confirmation)
    with confirmation.using("heinzel", "approve-connected-read", args):
        assert grants.approve(session, "req-one", review, proof, confirmation) == proposal
        assert grants.approve(session, "req-one", review, proof, confirmation) == proposal
    assert grants.state(session).status == "approved"
    competing_args = {**args, "approval_proof": competing}
    with confirmation.using("heinzel", "approve-connected-read", competing_args), pytest.raises(HeinzelError):
        grants.approve(session, "req-one", review, competing, confirmation)
    crud.accept(session, MessageRequest(request_id="after", text="Read the approved source."))
    result = asyncio.run(
        InteractiveTurn(crud).run(
            session, "after", _model("fictional", "Read the source."), limits=TurnLimits(), connected=grants
        )
    )
    assert result.text == "Read the source."
    assert state["reads"] == 1
    with crud.repository.main_writer(session) as writer:
        assert grants.read(session, writer) == {"events": ["fictional"]}
    assert state["reads"] == 2
    state["result"] = {"bad": float("nan")}
    with crud.repository.main_writer(session) as writer, pytest.raises(HeinzelError):
        grants.read(session, writer)
    del state["result"]
    assert state["reads"] == 3
    state["account"] = "account-b"
    with crud.repository.main_writer(session) as writer, pytest.raises(HeinzelError):
        grants.read(session, writer)
    assert state["reads"] == 3
    state["account"] = "account-a"
    state["scopes"] = ("calendar", "drive", "different")
    with crud.repository.main_writer(session) as writer, pytest.raises(HeinzelError):
        grants.read(session, writer)
    state["scopes"] = ("calendar", "drive")
    state["config"] = Config(files=(), data={"capabilities": {"calendar": "different"}})
    with crud.repository.main_writer(session) as writer, pytest.raises(HeinzelError):
        grants.read(session, writer)
    state["config"] = holder_config = grants.kernel.config
    assert holder_config is grants.kernel.config
    foreign = CallIdentity(Principal("u-" + "b" * 32), "api-bearer", "foreign", "private", frozenset({"private:write"}))
    with current.using(foreign), pytest.raises(HeinzelError):
        grants.issue(session, "req-one", "foreign")
    with current.local("mcp-stdio"), pytest.raises(HeinzelError):
        grants.issue(session, "req-one", "model")
    grants.revoke(session, "req-one")
    assert grants.state(session).status == "revoked"
    with crud.repository.main_writer(session) as writer, pytest.raises(HeinzelError):
        grants.read(session, writer)
    assert state["reads"] == 3


def test_unknown_or_foreign_session_never_opens_provider(connected):
    _current, _crud, _session, grants, fixed, state = connected
    with pytest.raises(HeinzelError):
        grants.propose("hz-01ARZ3NDEKTSV4RRFFQ69G5FAV", "req", "calendar", "events", fixed)
    assert state["opens"] == 0


def test_fixed_argument_comparison_rejects_json_type_coercion(monkeypatch):
    from types import SimpleNamespace

    from heinzel.modules.heinzel import _capability_review as module

    class Protocol:
        def read(self, count: int, flag: bool) -> None:
            pass

    monkeypatch.setattr(
        module, "CAPABILITIES", {"example": SimpleNamespace(methods={"read": "read"}, protocol=Protocol)}
    )
    assert module.validate_capability_read_arguments("example", "read", {"count": 1, "flag": True}) == {
        "count": 1,
        "flag": True,
    }
    for value in ({"count": True, "flag": True}, {"count": 1.0, "flag": True}, {"count": 1, "flag": 1}):
        with pytest.raises(HeinzelError):
            module.validate_capability_read_arguments("example", "read", value)


def test_provider_review_cannot_coerce_fixed_json_types(connected, monkeypatch):
    from heinzel.modules.heinzel import _connected_grants as module

    _current, _crud, session, grants, _fixed, state = connected
    monkeypatch.setattr(module, "validate_capability_read_arguments", lambda _c, _m, fixed: fixed)
    state["coerce"] = True
    with pytest.raises(HeinzelError, match="did not match"):
        grants.propose(session, "req-coercion", "calendar", "events", {"count": 1})
    assert state["reads"] == 0
