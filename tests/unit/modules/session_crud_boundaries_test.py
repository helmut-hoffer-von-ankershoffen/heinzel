"""Session CRUD names every refusal: schedule approvals, saved bodies that were damaged and foreign cursors."""

import pytest

from heinzel import CurrentUser, HeinzelError
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud, history_cursor_before
from heinzel.modules.heinzel._session_models import MessageRequest, SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from tests.unit.modules.session_crud_test import AT, _issue_test_approval

pytestmark = pytest.mark.unit

DOC = "https://docs.google.com/document/d/abc123_DEF456/edit"
OTHER_DOC = "https://docs.google.com/document/d/xyz987_GHI654/edit"


@pytest.fixture
def services(tmp_path):
    current = CurrentUser(tmp_path / "state" / "identity")
    repository = PersonalSessionRepository(current, clock=lambda: AT)
    service = SessionCrud(repository, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2), clock=lambda: AT)
    with current.local("cli"):
        yield service, repository, current


def forge(repository, session, kind, key, value, **data):
    with repository.control_writer(session) as writer:
        body = immutable_body(writer, "forged", value, control=True)
        writer.append(kind, key=key, data=body.model_dump(mode="json") | data)


def _proposed(service, request="check-in-1"):
    session = service.new_chat("create-1", "Ada").session_id
    schedule = service.propose_check_in(session, request, "Europe/Berlin").schedule
    arguments = {
        "session_id": session,
        "request_id": "activate-1",
        "action": "activate",
        "expected_revision": schedule.revision,
        "expected_contract_version": schedule.contract_version,
        "expected_preview_hash": schedule.preview_hash,
    }
    return session, schedule, arguments


def test_review_shows_the_exact_schedule_and_refuses_what_is_not_a_pending_action(services):
    service, repository, current = services
    session, schedule, arguments = _proposed(service)
    shown = service.review("heinzel", "schedule-control", arguments)
    assert shown == {
        "name": "Ada",
        "action": "activate",
        "summary": schedule.summary,
        "cron": "*/5 * * * *",
        "timezone": "Europe/Berlin",
        "status": "proposed",
        "revision": schedule.revision,
        "contract_version": schedule.contract_version,
        "preview_hash": schedule.preview_hash,
        "request_id": "activate-1",
    }
    refused = [
        ("other", "schedule-control", arguments),
        ("heinzel", "other", arguments),
        ("heinzel", "schedule-control", arguments | {"approval_proof": "supplied"}),
        ("heinzel", "schedule-control", {k: v for k, v in arguments.items() if k != "action"}),
        ("heinzel", "schedule-control", arguments | {"session_id": "not a session"}),
        ("heinzel", "schedule-control", arguments | {"action": "pause"}),
        ("heinzel", "schedule-control", arguments | {"action": 3}),
        ("heinzel", "schedule-control", arguments | {"expected_revision": 0}),
        ("heinzel", "schedule-control", arguments | {"expected_revision": True}),
        ("heinzel", "schedule-control", arguments | {"expected_contract_version": 0}),
        ("heinzel", "schedule-control", arguments | {"expected_preview_hash": "abc"}),
        ("heinzel", "schedule-control", arguments | {"expected_preview_hash": "Z" * 64}),
        ("heinzel", "schedule-control", arguments | {"expected_preview_hash": 5}),
    ]
    for module, operation, bad in refused:
        with pytest.raises(HeinzelError, match="invalid or unavailable"):
            service.review(module, operation, bad)
    for field, value in (
        ("expected_revision", 9),
        ("expected_contract_version", 9),
        ("expected_preview_hash", "0" * 64),
    ):
        with pytest.raises(HeinzelError, match="changed; reload before reviewing"):
            service.review("heinzel", "schedule-control", arguments | {field: value})
    proof = _issue_test_approval(
        repository, current, session, "activate-1", schedule.revision, "activate", schedule.preview_hash
    )
    service.schedule_control(
        session, "activate-1", schedule.revision, "activate", proof, schedule.contract_version, schedule.preview_hash
    )
    with pytest.raises(HeinzelError, match="already recorded"):
        service.review("heinzel", "schedule-control", arguments)
    assert service.applied("heinzel", "schedule-control", arguments)


def test_issue_refuses_a_malformed_or_stale_review_before_a_proof_exists(services):
    service, repository, _current = services
    session, schedule, arguments = _proposed(service)
    for bad in (
        {k: v for k, v in arguments.items() if k != "expected_preview_hash"},
        arguments | {"action": "pause"},
        arguments | {"expected_revision": -1},
        arguments | {"expected_preview_hash": "g" * 64},
    ):
        with pytest.raises(HeinzelError, match="invalid or unavailable"):
            service.issue("heinzel", "schedule-control", bad, "test")
    for field, value in (
        ("expected_revision", 7),
        ("expected_contract_version", 7),
        ("expected_preview_hash", "0" * 64),
    ):
        with pytest.raises(HeinzelError, match="changed; reload before approving"):
            service.issue("heinzel", "schedule-control", arguments | {field: value}, "test")
    resume = arguments | {"action": "resume"}
    with pytest.raises(HeinzelError, match="changed; reload before approving"):
        service.issue("heinzel", "schedule-control", resume, "test")
    assert not [e for e in repository.control_read(session).events if e.kind == "approval.issued"]
    assert schedule.status == "proposed"


def test_applied_reconciles_only_the_exact_recorded_action(services):
    service, repository, current = services
    session, schedule, arguments = _proposed(service)
    assert not service.applied("heinzel", "schedule-control", arguments)
    for module, operation, bad in (
        ("other", "schedule-control", arguments),
        ("heinzel", "schedule-control", arguments | {"approval_proof": "x"}),
        ("heinzel", "schedule-control", {k: v for k, v in arguments.items() if k != "action"}),
        ("heinzel", "schedule-control", arguments | {"action": "delete"}),
        ("heinzel", "schedule-control", arguments | {"expected_revision": 0}),
        ("heinzel", "schedule-control", arguments | {"expected_contract_version": 0}),
        ("heinzel", "schedule-control", arguments | {"expected_preview_hash": 3}),
    ):
        with pytest.raises(HeinzelError, match="cannot be reconciled safely"):
            service.applied(module, operation, bad)
    proof = _issue_test_approval(
        repository, current, session, "activate-1", schedule.revision, "activate", schedule.preview_hash
    )
    service.schedule_control(
        session, "activate-1", schedule.revision, "activate", proof, schedule.contract_version, schedule.preview_hash
    )
    assert service.applied("heinzel", "schedule-control", arguments)
    assert not service.applied("heinzel", "schedule-control", arguments | {"expected_revision": 5})
    assert not service.applied("heinzel", "schedule-control", arguments | {"action": "resume"})
    paused = service.schedule_control(session, "pause-1", schedule.revision + 1, "pause")
    pause = arguments | {"request_id": "pause-1", "action": "pause", "expected_revision": paused.schedule.revision - 1}
    assert service.applied("heinzel", "schedule-control", pause)
    assert not service.applied("heinzel", "schedule-control", pause | {"expected_revision": 9})


def test_schedule_control_refuses_a_stale_revision_a_wrong_state_or_a_missing_proposal(services):
    service, repository, current = services
    empty = service.new_chat("create-empty").session_id
    with pytest.raises(HeinzelError, match="Propose a check-in before activating"):
        service.schedule_control(empty, "pause-empty", 1, "pause")
    session, schedule, _arguments = _proposed(service)
    with pytest.raises(HeinzelError, match="Reload the schedule"):
        service.schedule_control(session, "bad-version", 1, "pause", expected_contract_version=0)
    stale = _issue_test_approval(
        repository, current, session, "activate-1", schedule.revision + 4, "activate", schedule.preview_hash
    )
    with pytest.raises(HeinzelError, match="changed; reload before controlling"):
        service.schedule_control(
            session,
            "activate-1",
            schedule.revision + 4,
            "activate",
            stale,
            schedule.contract_version,
            schedule.preview_hash,
        )
    proof = _issue_test_approval(
        repository, current, session, "activate-2", schedule.revision, "activate", schedule.preview_hash
    )
    active = service.schedule_control(
        session, "activate-2", schedule.revision, "activate", proof, schedule.contract_version, schedule.preview_hash
    )
    again = _issue_test_approval(
        repository, current, session, "activate-3", active.schedule.revision, "activate", schedule.preview_hash
    )
    with pytest.raises(HeinzelError, match="Cannot activate a schedule that is active"):
        service.schedule_control(
            session,
            "activate-3",
            active.schedule.revision,
            "activate",
            again,
            schedule.contract_version,
            schedule.preview_hash,
        )


def test_saved_bodies_that_were_damaged_are_named_and_never_guessed(services):
    service, repository, _current = services
    session = service.new_chat("create-1", "Ada").session_id
    junk = {"unexpected": 1}
    forge(repository, session, "skill.draft", "skill-draft-bad", junk)
    with pytest.raises(HeinzelError, match="skill draft is invalid"):
        service.skill_draft(session, "bad")
    forge(repository, session, "skill.sharing", "skill-sharing-bad", junk)
    with pytest.raises(HeinzelError, match="sharing proposal is invalid"):
        service.skill_sharing(session, "bad")
    forge(repository, session, "profile.proposed", "profile-proposal-bad", junk)
    with pytest.raises(HeinzelError, match="profile proposal is invalid"):
        service.latest_profile_proposal(session)
    forge(repository, session, "agent.proposed", "not-an-agent-proposal", junk)
    with pytest.raises(HeinzelError, match="agent execution proposal is invalid"):
        service.latest_scheduled_agent_proposal(session)
    forge(repository, session, "agent.proposed", "agent-proposal-bad", junk)
    with pytest.raises(HeinzelError, match="agent execution proposal is invalid"):
        service.latest_scheduled_agent_proposal(session)
    assert service.snapshot(session).name == "Ada"


def test_damaged_contract_schedule_or_profile_stops_the_snapshot(tmp_path):
    for kind, key, match in (
        ("contract.proposed", "contract-bad", "contract version is invalid"),
        ("schedule.pause", "schedule-control-bad", "saved schedule is invalid"),
        ("profile.updated", "profile-bad", "saved profile is invalid"),
    ):
        current = CurrentUser(tmp_path / kind / "identity")
        repository = PersonalSessionRepository(current, clock=lambda: AT)
        service = SessionCrud(
            repository, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2), clock=lambda: AT
        )
        with current.local("cli"):
            session = service.new_chat("create-1", "Ada").session_id
            forge(repository, session, kind, key, {"unexpected": 1}, revision=9)
            with pytest.raises(HeinzelError, match=match):
                service.snapshot(session)


def test_a_saved_profile_needs_a_positive_revision_and_an_existing_profile_event(services):
    service, repository, _current = services
    session = service.new_chat("create-1", "Ada").session_id
    with pytest.raises(HeinzelError, match="creation is incomplete"):
        service._profile(session, [])
    forge(repository, session, "profile.updated", "profile-zero", {"name": "Ada", "appearance": {}}, revision=0)
    with pytest.raises(HeinzelError, match="profile revision is invalid"):
        service.snapshot(session)


def test_artifact_associations_that_were_tampered_with_are_refused(services):
    service, repository, _current = services
    session = service.new_chat("create-1").session_id
    first = service.associate_artifact(session, "add-one", "document", DOC)
    forge(
        repository,
        session,
        "artifact.associated",
        "artifact-association-add-two",
        first.model_copy(update={"request_id": "add-two"}).model_dump(mode="json"),
    )
    with pytest.raises(HeinzelError, match="artifact associations are invalid"):
        service.personal_artifacts(session)
    other = service.new_chat("create-2").session_id
    real = service.associate_artifact(other, "add-one", "document", OTHER_DOC)
    forge(repository, other, "artifact.associated", "artifact-association-renamed", real.model_dump(mode="json"))
    with pytest.raises(HeinzelError, match="association is invalid"):
        service.personal_artifacts(other)


def test_accepted_messages_with_forged_provenance_or_identity_are_refused(services):
    service, repository, _current = services
    session = service.new_chat("create-1").session_id
    service.accept(session, MessageRequest(request_id="m1", text="first"))
    forge(repository, session, "message.accepted", "m-bogus", {"request_id": "m-bogus", "text": "x"})
    with pytest.raises(HeinzelError, match="invalid message identity"):
        service.recent_history(session)
    other = service.new_chat("create-2").session_id
    forge(
        repository, other, "message.accepted", "m-bogus", {"request_id": "m-bogus", "text": "x"}, message_id="m-bogus"
    )
    forge(
        repository,
        other,
        "message.accepted",
        "m-bad-origin",
        {"request_id": "m-bad-origin", "text": "x"},
        message_id="m-bad-origin",
        origin="bogus",
    )
    with pytest.raises(HeinzelError, match="invalid acceptance provenance"):
        service.recent_history(other, limit=1)
    third = service.new_chat("create-3").session_id
    forge(
        repository, third, "message.accepted", "m-moved", {"request_id": "m-other", "text": "x"}, message_id="m-moved"
    )
    with pytest.raises(HeinzelError, match="no longer matches its checkpoint"):
        service.recent_history(third)


def test_a_failed_scheduled_turn_reads_as_a_system_message_and_history_limits_are_bounded(services):
    service, repository, _current = services
    session = service.new_chat("create-1").session_id
    for number in range(3):
        service.accept(session, MessageRequest(request_id=f"m{number}", text=f"text {number}"))
    with repository.control_writer(session) as writer:
        writer.append("agent.schedule-failed", key="failed-1", data={"message_id": "failed-1", "reason": "no model"})
    recent = service.recent_history(session, limit=2)
    assert [(m.role, m.origin, m.status) for m in recent] == [
        ("user", "cli", "pending"),
        ("system", "daemon", "failed"),
    ]
    assert recent[-1].text == "A scheduled turn did not run: no model"
    for limit in (0, 51, True):
        with pytest.raises(HeinzelError, match="Choose between 1 and 50"):
            service.recent_history(session, limit=limit)


def test_a_search_position_is_bound_to_its_journal_prefix(services):
    service, repository, _current = services
    session = service.new_chat("create-1").session_id
    for number in range(3):
        service.accept(session, MessageRequest(request_id=f"m{number}", text=f"text {number}"))
    journal = repository.control_read(session)
    target = next(e.seq for e in journal.events if e.data.get("message_id") == "m1" and e.kind == "message.accepted")
    high = journal.events[-1].seq
    cursor = history_cursor_before(service, session, target, high, journal.hashes[high - 1])
    page = service.snapshot(session, cursor=cursor)
    assert [m.id for m in page.messages] == ["m1", "m2"]
    for bad in (0, high + 1, True):
        with pytest.raises(HeinzelError, match="search message position is invalid"):
            history_cursor_before(service, session, bad, high, journal.hashes[high - 1])
    with pytest.raises(HeinzelError, match="search message position is invalid"):
        history_cursor_before(service, session, target, high, "")
    wrong_prefix = history_cursor_before(service, session, target, high, "0" * 64)
    wrong_target = history_cursor_before(service, session, 1, high, journal.hashes[high - 1])
    beyond = history_cursor_before(service, session, target, high + 5, journal.hashes[high - 1])
    other = service.new_chat("create-2").session_id
    for bad in ("a" * 2000, "!!not-base64!!", wrong_prefix, wrong_target, beyond, cursor):
        with pytest.raises(HeinzelError, match="different conversation or is invalid"):
            service.snapshot(other if bad == cursor else session, cursor=bad)
