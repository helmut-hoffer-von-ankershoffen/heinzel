"""Chat-created inert proposal through reviewed, source-bound fake scheduled notices."""

import sys
from datetime import UTC, datetime

import pytest

from heinzel import ConnectedItemIdentity, Notifications
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


@pytest.mark.timeout(60)  # a complete chat, approval and restart journey with several model subprocesses
def test_chat_contract_approved_source_and_item_notices_survive_restart_and_revoke(
    make_kernel, tmp_path, monkeypatch, settings
):
    now = freeze_clock(monkeypatch, datetime(2026, 5, 4, 12, 2, 0, tzinfo=UTC))

    client, kernel, current, session, state = _fixture(make_kernel, tmp_path, monkeypatch)
    state["item_identity"] = ConnectedItemIdentity(collection=("events",), identity_field="id", revision_field="rev")
    state["events"] = [{"id": "provider-item", "rev": "one"}]
    notices = Notices()
    kernel.bind(Notifications, notices)
    executable = tmp_path / "fake-claude"
    assert executable.exists()
    assert executable.resolve().is_relative_to(tmp_path.resolve())
    assert not executable.samefile(sys.executable)
    assert not executable.samefile(sys._base_executable)
    write_model(executable, tmp_path / "journey-calls")
    proposal = chat_proposal(client, session, "Propose an agent for my source")
    assert state["reads"] == 0
    assert notices.calls == []
    confirm_contract(client, session, proposal)
    _approve_read(client, kernel, current, session, state, rolling=True)
    schedule = propose_schedule(client, session, proposal)
    refuse_tick_before_confirmation(kernel, current, session, now[0])
    assert state["reads"] == 0
    assert notices.calls == []
    _confirm_item_schedule(client, session, schedule)
    assert_ticks((client, kernel, current, session, state, notices, settings, monkeypatch, now))
