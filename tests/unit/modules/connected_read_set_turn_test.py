"""A human-approved set reaches one host-fenced interactive read and durable receipt."""

import asyncio
import contextlib

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from heinzel import HeinzelError
from heinzel.modules.heinzel._engine import TurnLimits
from heinzel.modules.heinzel._interactive_turn import InteractiveTurn
from heinzel.modules.heinzel._session_models import MessageRequest
from tests.unit.modules.connected_read_set_preview_test import _approved

pytestmark = pytest.mark.unit
pytest_plugins = ("tests.unit.modules.connected_grants_test",)


def _model(handle, expected):
    calls = 0

    def respond(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(
                [ToolCallPart("use_tool", {"name": "connected_read_set", "arguments": {"handle": handle}})]
            )
        assert any(
            isinstance(part, ToolReturnPart) and expected in str(part.content).lower()
            for message in messages
            for part in message.parts
        )
        return ModelResponse([ToolCallPart(info.output_tools[0].name, {"report": "Inspected", "summary": "Inspected"})])

    return FunctionModel(respond)


def test_human_approved_set_routes_exact_handle_and_records_receipt(connected):
    _, crud, session, grants, state, _, _, review, approvals = _approved(connected)
    handle = review.proposal.entries[0].handle
    crud.accept(session, MessageRequest(request_id="read-set-turn", text="Read the approved source."))
    result = asyncio.run(
        InteractiveTurn(crud).run(
            session,
            "read-set-turn",
            _model(handle, "fictional"),
            limits=TurnLimits(),
            connected=grants,
            connected_set=approvals,
        )
    )
    assert result.text == "Inspected"
    assert state["reads"] == 1
    events = crud.repository.control_read(session).events
    receipts = [event for event in events if event.kind == "connected-set-input.observed"]
    assert len(receipts) == 1
    requested = [
        event for event in events if event.kind == "interactive.tool" and event.data.get("tool") == "connected_read_set"
    ]
    assert [event.data["status"] for event in requested] == ["requested", "completed"]
    assert requested[0].data["handle"] == handle
    assert requested[0].turn == receipts[0].turn
    assert "fictional" not in str(receipts[0].data)
    activity = InteractiveTurn(crud).activity(session)
    assert [item.status for item in activity if item.tool == "connected_read_set"] == ["requested", "completed"]
    assert handle not in str(activity)
    assert grants.tool_schema(session) is None


def test_unapproved_set_does_not_expose_or_execute_tool(connected):
    from tests.unit.modules.connected_read_set_approval_test import _prepare

    _, crud, session, grants, state, _, _, review, approvals = _prepare(connected)
    crud.accept(session, MessageRequest(request_id="unapproved-set", text="Read the source."))
    result = asyncio.run(
        InteractiveTurn(crud).run(
            session,
            "unapproved-set",
            _model(review.proposal.entries[0].handle, "unavailable"),
            limits=TurnLimits(),
            connected=grants,
            connected_set=approvals,
        )
    )
    assert result.text == "Inspected"
    assert state["reads"] == 0
    assert not any(
        event.kind == "connected-set-input.observed" for event in crud.repository.control_read(session).events
    )


def test_unaccepted_turn_never_previews_set(connected, monkeypatch):
    _, crud, session, grants, state, _, _, review, approvals = _approved(connected)

    def forbidden(*_args):
        pytest.fail("A turn without an accepted message must not preview the set")

    monkeypatch.setattr("heinzel.modules.heinzel._interactive_turn.read_set_tool_preview", forbidden)
    with pytest.raises(HeinzelError, match="No accepted message"):
        asyncio.run(
            InteractiveTurn(crud).run(
                session,
                "missing-message",
                _model(review.proposal.entries[0].handle, "unavailable"),
                limits=TurnLimits(),
                connected=grants,
                connected_set=approvals,
            )
        )
    assert state["reads"] == 0


def test_unknown_handle_cannot_read_or_record(connected):
    _, crud, session, grants, state, _, _, _, approvals = _approved(connected)
    crud.accept(session, MessageRequest(request_id="wrong-handle", text="Read."))
    asyncio.run(
        InteractiveTurn(crud).run(
            session,
            "wrong-handle",
            _model("read-" + "0" * 32, "unavailable"),
            limits=TurnLimits(),
            connected=grants,
            connected_set=approvals,
        )
    )
    assert state["reads"] == 0
    assert not any(
        event.kind == "connected-set-input.observed" for event in crud.repository.control_read(session).events
    )


def test_daemon_cannot_read_the_human_set(connected):
    current, crud, session, grants, state, _, _, review, approvals = _approved(connected)
    crud.accept(session, MessageRequest(request_id="daemon-read", text="Read."))
    with current.local("daemon"), contextlib.suppress(HeinzelError):
        asyncio.run(
            InteractiveTurn(crud).run(
                session,
                "daemon-read",
                _model(review.proposal.entries[0].handle, "unavailable"),
                limits=TurnLimits(),
                connected=grants,
                connected_set=approvals,
            )
        )
    assert state["reads"] == 0
    assert not any(
        event.kind == "connected-set-input.observed" for event in crud.repository.control_read(session).events
    )


@pytest.mark.parametrize("failure", ["changed-account", "review-error", "malformed"])
def test_same_lease_postread_drift_is_failed_without_receipt(connected, monkeypatch, failure):
    from heinzel import ConnectedReadService

    _, crud, session, grants, state, _, _, review, approvals = _approved(connected)
    handle = review.proposal.entries[0].handle
    _record, provider = grants.kernel.selected_capability_service(ConnectedReadService, "calendar")
    open_original = provider.open_lease

    def open_lease(*args):
        lease = open_original(*args)
        read_original, review_original = lease.read, lease.review

        def read(arguments):
            value = read_original(arguments)

            def changed_review():
                if failure == "review-error":
                    msg = "private provider error"
                    raise HeinzelError(msg)
                if failure == "malformed":
                    return {}
                return review_original().model_copy(update={"account_identity": "different-account"})

            lease.review = changed_review
            return value

        lease.read = read
        return lease

    monkeypatch.setattr(provider, "open_lease", open_lease)
    crud.accept(session, MessageRequest(request_id="read-drift", text="Read the approved source."))
    result = asyncio.run(
        InteractiveTurn(crud).run(
            session,
            "read-drift",
            _model(handle, "did not produce"),
            limits=TurnLimits(),
            connected=grants,
            connected_set=approvals,
        )
    )
    assert result.text == "Inspected"
    assert state["reads"] == 1
    events = crud.repository.control_read(session).events
    activity = [event.data.get("status") for event in events if event.kind == "interactive.tool"]
    assert activity == ["requested", "failed"]
    assert not any(event.kind == "connected-set-input.observed" for event in events)
    assert "private provider error" not in str(events)
