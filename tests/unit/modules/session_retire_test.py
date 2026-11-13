"""Retire hides a personal Heinzel and keeps its history; restore brings it back (ADR 0049 §7)."""

from datetime import UTC, datetime

import pytest

from heinzel import CurrentUser, HeinzelError, JobInactive
from heinzel.modules.heinzel._session_crud import SessionCrud, is_retired
from heinzel.modules.heinzel._session_models import MessageRequest, SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit

AT = datetime(2026, 11, 13, tzinfo=UTC)


@pytest.fixture
def service(tmp_path):
    current = CurrentUser(tmp_path / "state" / "identity")
    crud = SessionCrud(
        PersonalSessionRepository(current, clock=lambda: AT),
        lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2),
        clock=lambda: AT,
    )
    with current.local("cli"):
        yield crud


def test_retire_keeps_history_and_restore_brings_the_heinzel_back(service):
    created = service.new_chat("create-ada", "Ada", initial_message="Hello Ada")
    before = service.snapshot(created.session_id).messages

    retired = service.retire(created.session_id, "retire-1")

    assert retired.state == "retired"
    assert retired.messages == before
    assert [(item.session_id, item.state) for item in service.listing().sessions] == [(created.session_id, "retired")]
    restored = service.retire(created.session_id, "restore-1", retired=False)
    assert restored.state == "proposed"
    assert restored.messages == before
    assert not is_retired(service.repository.control_read(created.session_id).events)


def test_retire_and_restore_replay_exactly_and_refuse_a_reused_request(service):
    created = service.new_chat("create-bo", "Bo")
    first = service.retire(created.session_id, "retire-1")
    assert service.retire(created.session_id, "retire-1") == first
    with pytest.raises(HeinzelError, match="different retire or restore"):
        service.retire(created.session_id, "retire-1", retired=False)
    events = service.repository.control_read(created.session_id).events
    assert [event.kind for event in events].count("heinzel.retired") == 1


def test_a_retired_heinzel_takes_no_new_message_but_a_replay_of_an_accepted_one_still_answers(service):
    created = service.new_chat("create-cy", "Cy")
    accepted = service.accept(created.session_id, MessageRequest(request_id="m-1", text="First"))
    service.retire(created.session_id, "retire-1")

    assert service.accept(created.session_id, MessageRequest(request_id="m-1", text="First")) == accepted
    with pytest.raises(HeinzelError, match="retired"):
        service.accept(created.session_id, MessageRequest(request_id="m-2", text="Second"))
    service.retire(created.session_id, "restore-1", retired=False)
    assert service.accept(created.session_id, MessageRequest(request_id="m-2", text="Second")).request_id == "m-2"


def test_a_retired_heinzel_does_not_check_in(service):
    created = service.new_chat("create-di", "Di")
    service.retire(created.session_id, "retire-1")

    outcome = service.emit_check_in(created.session_id, 1, 1, AT, notifications=None)

    assert isinstance(outcome, JobInactive)
    assert outcome.reason == "this Heinzel is retired"


def test_retire_needs_a_completed_heinzel_of_this_owner(service):
    with pytest.raises(HeinzelError):
        service.retire("hz-01JC0000000000000000000000", "retire-1")
