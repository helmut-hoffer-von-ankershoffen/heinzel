"""Chat-created inert proposal through reviewed, source-bound fake scheduled notices."""

import hashlib
import sys
from datetime import UTC, datetime

import pytest

from heinzel import ChatDraftTemplate, ConnectedItemIdentity, Module, Notifications, PlaybookLet
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from tests.integration._scheduled_journey import (
    Notices,
    assert_ticks,
    chat_proposal,
    confirm_contract,
    freeze_clock,
    propose_schedule,
    refuse_tick_before_confirmation,
    write_model,
)
from tests.integration.scheduled_connected_read_test import (
    _approve_read,
    _confirm_item_schedule,
    _fixture,
)

pytestmark = pytest.mark.integration

MESSAGE = "Help me keep a private summary of my source current"


@pytest.mark.timeout(60)  # a complete chat, approval and restart journey with several model subprocesses
def test_chat_contract_approved_source_and_item_notices_survive_restart_and_revoke(
    make_kernel, tmp_path, monkeypatch, settings
):
    now = freeze_clock(monkeypatch, datetime(2026, 5, 4, 12, 2, 0, tzinfo=UTC))

    source = "Ask what source and result the person wants before proposing a draft."
    digest = hashlib.sha256(source.encode()).hexdigest()
    draft = Module("draftfixture", "Inert generic draft")
    draft.add(
        PlaybookLet(name="draftfixture", summary="Fixture", chat_drafts=(ChatDraftTemplate(name="draft", text=source),))
    )

    def with_draft(**kwargs):
        return make_kernel(**kwargs, draftfixture=draft)

    client, kernel, current, session, state = _fixture(with_draft, tmp_path, monkeypatch)
    state["item_identity"] = ConnectedItemIdentity(collection=("events",), identity_field="id", revision_field="rev")
    state["events"] = [{"id": "provider-item", "rev": "one"}]
    notices = Notices()
    kernel.bind(Notifications, notices)
    executable = tmp_path / "fake-claude"
    assert executable.exists()
    assert executable.resolve().is_relative_to(tmp_path.resolve())
    assert not executable.samefile(sys.executable)
    assert not executable.samefile(sys._base_executable)
    write_model(executable, tmp_path / "journey-calls", digest=digest)
    proposal = chat_proposal(client, session, MESSAGE)
    assert "draftfixture:draft" in proposal["contract"]
    assert state["reads"] == 0
    assert notices.calls == []
    with current.local("cli"):
        crud = kernel.resolve(SessionCrud)
        reopened = SessionCrud(PersonalSessionRepository(current), crud.costs)
        assert reopened.new_chat("new").session_id == session
        assert reopened.latest_scheduled_agent_proposal(session).model_dump(mode="json") == proposal
        assert any(
            message.role == "user" and message.text == MESSAGE for message in reopened.snapshot(session).messages
        )
        events = crud.repository.control_read(session).events
        assert not any(
            event.kind in {"approval.issued", "agent.contract-accepted", "agent.schedule-approved"} for event in events
        )
    confirm_contract(client, session, proposal)
    _approve_read(client, kernel, current, session, state, rolling=True)
    schedule = propose_schedule(client, session, proposal)
    refuse_tick_before_confirmation(kernel, current, session, now[0])
    assert state["reads"] == 0
    assert notices.calls == []
    _confirm_item_schedule(client, session, schedule)
    assert_ticks((client, kernel, current, session, state, notices, settings, monkeypatch, now))
