"""A personal Heinzel's journal refuses bad input and tampered private snapshots instead of guessing."""

import base64
from datetime import UTC, datetime

import pytest

from heinzel import CurrentUser, HeinzelError, JobInactive
from heinzel.modules.heinzel import _session_crud
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud, history_cursor_before
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


def _forge(crud, session, kind, key, value, **data):
    """Append an event whose private body is well-formed storage holding the given (wrong) content."""
    with crud.repository.control_writer(session) as writer:
        body = immutable_body(writer, kind.replace(".", "-"), value, control=True)
        writer.append(kind, key=key, data={**body.model_dump(mode="json"), **data})


def _cursor(session, after):
    raw = _session_crud._Cursor(v=1, session_id=session, after=after).model_dump_json().encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_paging_and_history_bounds_are_refused(service):
    created = service.new_chat("guard-paging", "Pager")
    for limit in (0, 201):
        with pytest.raises(HeinzelError, match="between 1 and 200"):
            service.snapshot(created.session_id, limit=limit)
    with pytest.raises(HeinzelError, match="ahead of the committed conversation"):
        service.snapshot(created.session_id, cursor=_cursor(created.session_id, 999))
    with pytest.raises(HeinzelError, match="different conversation or is invalid"):
        service.snapshot(created.session_id, cursor="not*base64")
    with pytest.raises(HeinzelError, match="different conversation or is invalid"):
        service.snapshot(created.session_id, cursor="x" * 5000)
    with pytest.raises(HeinzelError, match="recent messages"):
        service.recent_history(created.session_id, limit=0)
    with pytest.raises(HeinzelError, match="search message position"):
        history_cursor_before(service, created.session_id, 0, 1, "a" * 64)


def test_a_message_with_attachments_needs_a_retained_upload(service):
    created = service.new_chat("guard-attach", "Attach")
    request = MessageRequest.model_construct(
        request_id="m-1", text="With a file", when_busy="follow_up", attachment_ids=["upload-1"]
    )
    with pytest.raises(HeinzelError, match="retained upload"):
        service.accept(created.session_id, request)


def test_a_tampered_profile_is_refused(service):
    created = service.new_chat("guard-profile", "Profile")
    _forge(service, created.session_id, "profile.updated", "forged-1", {"name": 7}, revision=2)
    with pytest.raises(HeinzelError, match="saved profile is invalid"):
        service.snapshot(created.session_id)
    other = service.new_chat("guard-profile-2", "Profile two")
    _forge(service, other.session_id, "profile.updated", "forged-2", {"name": "Fine"}, revision=0)
    with pytest.raises(HeinzelError, match="revision is invalid"):
        service.snapshot(other.session_id)
    third = service.new_chat("guard-profile-3", "Profile three")
    with service.repository.control_writer(third.session_id) as writer:
        writer.append("profile.updated", key="no-body", data={"revision": 2})
    with pytest.raises(HeinzelError, match="invalid private body reference"):
        service.snapshot(third.session_id)


def test_a_reserved_heinzel_without_a_profile_asks_for_its_creation_retry(service):
    session = service.repository.reserve("guard-half", "a" * 64)
    with service.repository.main_writer(session, wait=True) as main:
        main.append("session.created", key="created", data={"owner_id": service.repository.principal_id})
    with service.repository.control_writer(session) as writer:
        writer.append("note", key="note", data={})
    with pytest.raises(HeinzelError, match="creation is incomplete"):
        service.snapshot(session)


def test_tampered_artifacts_drafts_and_proposals_are_refused(service):
    created = service.new_chat("guard-records", "Records")
    session = created.session_id
    with pytest.raises(HeinzelError, match="does not exist"):
        service.skill_sharing(session, "missing")
    with pytest.raises(HeinzelError, match="Invalid sharing proposal page"):
        service.skill_sharing_proposals(session, cursor=-1)
    with pytest.raises(HeinzelError, match="Invalid draft cursor"):
        service.skill_drafts(session, cursor=-1)
    _forge(service, session, "artifact.associated", "artifact-association-a", {"kind": "nope"})
    with pytest.raises(HeinzelError, match="artifact association is invalid"):
        service.personal_artifacts(session)
    _forge(service, session, "skill.sharing", "skill-sharing-s1", {"nope": True})
    with pytest.raises(HeinzelError, match="sharing proposal is invalid"):
        service.skill_sharing(session, "s1")
    _forge(service, session, "skill.draft", "skill-draft-d1", {"nope": True})
    with pytest.raises(HeinzelError, match="skill draft is invalid"):
        service.skill_draft(session, "d1")
    _forge(service, session, "profile.proposed", "profile-proposal-p1", {"nope": True})
    with pytest.raises(HeinzelError, match="profile proposal is invalid"):
        service.latest_profile_proposal(session)
    _forge(service, session, "agent.proposed", "agent-proposal-a1", {"nope": True})
    with pytest.raises(HeinzelError, match="agent execution proposal is invalid"):
        service.latest_scheduled_agent_proposal(session)
    with service.repository.control_writer(session) as writer:
        writer.append("agent.proposed", key="unkeyed", data={})
    with pytest.raises(HeinzelError, match="agent execution proposal is invalid"):
        service.latest_scheduled_agent_proposal(session)


def test_a_tampered_contract_is_refused(service):
    created = service.new_chat("guard-contract", "Contract")
    _forge(service, created.session_id, "contract.proposed", "forged-contract", {"contract": 3})
    with pytest.raises(HeinzelError, match="saved contract version is invalid"):
        service.snapshot(created.session_id)


def test_profile_proposals_follow_the_revision_and_their_request(service):
    created = service.new_chat("guard-proposal", "Proposal")
    first = service.propose_profile(created.session_id, "p-1", 1, "Mira", created.appearance)
    assert service.propose_profile(created.session_id, "p-1", 1, "Mira", created.appearance) == first
    with pytest.raises(HeinzelError, match="different details"):
        service.propose_profile(created.session_id, "p-1", 1, "Other", created.appearance)
    with pytest.raises(HeinzelError, match="profile changed"):
        service.propose_profile(created.session_id, "p-2", 5, "Mira", created.appearance)


def test_scheduled_agent_amendments_need_the_current_revision(service):
    created = service.new_chat("guard-agent", "Agent")
    service.propose_scheduled_agent(created.session_id, "agent-1", "Keep notes current.", 0.5, 30)
    with pytest.raises(HeinzelError, match="Reload the agent execution proposal"):
        service.amend_scheduled_agent(created.session_id, "agent-2", 0, "Keep notes current.", 0.5, 30)
    with pytest.raises(HeinzelError, match="changed; reload it"):
        service.amend_scheduled_agent(created.session_id, "agent-3", 9, "Keep notes current.", 0.5, 30)


def test_check_in_proposals_and_controls_refuse_invalid_or_stale_requests(service):
    created = service.new_chat("guard-check-in", "Check")
    session = created.session_id
    with pytest.raises(HeinzelError, match="IANA time zone"):
        service.propose_check_in(session, "c-0", " ")
    proposed = service.propose_check_in(session, "c-1", "UTC")
    with pytest.raises(HeinzelError, match="different contract proposal"):
        service.propose_check_in(session, "c-1", "Europe/Berlin")
    with pytest.raises(HeinzelError, match="one immutable check-in contract"):
        service.propose_check_in(session, "c-2", "UTC")
    with pytest.raises(HeinzelError, match="Choose activate, pause or resume"):
        service.schedule_control(session, "s-1", 1, "stop")
    with pytest.raises(HeinzelError, match="Reload the schedule"):
        service.schedule_control(session, "s-2", 1, "activate", expected_contract_version=0)
    with pytest.raises(HeinzelError, match="Reload the schedule"):
        service.schedule_control(session, "s-3", 0, "pause")
    with pytest.raises(HeinzelError, match="Cannot pause a schedule that is proposed"):
        service.schedule_control(session, "s-4", proposed.schedule.revision, "pause")
    with pytest.raises(HeinzelError, match="Propose a check-in before"):
        service.schedule_control(service.new_chat("guard-empty", "Empty").session_id, "s-5", 1, "pause")


@pytest.mark.parametrize(
    ("module", "operation", "arguments"),
    [
        ("other", "schedule-control", {}),
        ("heinzel", "schedule-control", {"approval_proof": "x"}),
        ("heinzel", "schedule-control", {"session_id": "nope"}),
    ],
)
def test_approval_review_issue_and_applied_refuse_foreign_or_malformed_requests(service, module, operation, arguments):
    with pytest.raises(HeinzelError, match="invalid or unavailable"):
        service.review(module, operation, arguments)
    with pytest.raises(HeinzelError, match="invalid or unavailable"):
        service.issue(module, operation, arguments, "test")
    with pytest.raises(HeinzelError, match="cannot be reconciled safely"):
        service.applied(module, operation, arguments)


def test_a_check_in_slot_without_a_time_zone_is_inactive(service):
    created = service.new_chat("guard-slot", "Slot")
    outcome = service.emit_check_in(created.session_id, 1, 1, datetime(2026, 11, 13), notifications=None)  # noqa: DTZ001 - the naive slot is the case
    assert isinstance(outcome, JobInactive)


def _body_of(crud, session, kind):
    event = next(item for item in crud.repository.control_read(session).events if item.kind == kind)
    return crud.read_body(session, event)


def test_copied_or_rekeyed_records_are_refused(service):
    created = service.new_chat("guard-copies", "Copies")
    session = created.session_id
    service.associate_artifact(
        session, "file-1", "document", "https://docs.google.com/document/d/abcdefghijklmnopqrstuvwxyz0123/edit"
    )
    artifact = _body_of(service, session, "artifact.associated")
    _forge(service, session, "artifact.associated", "artifact-association-file-2", {**artifact, "request_id": "file-2"})
    with pytest.raises(HeinzelError, match="associations are invalid"):
        service.personal_artifacts(session)
    other = service.new_chat("guard-rekeyed", "Rekeyed").session_id
    service.associate_artifact(
        other, "file-1", "document", "https://docs.google.com/document/d/abcdefghijklmnopqrstuvwxyz0123/edit"
    )
    _forge(
        service,
        other,
        "artifact.associated",
        "artifact-association-wrong",
        _body_of(service, other, "artifact.associated"),
    )
    with pytest.raises(HeinzelError, match="artifact association is invalid"):
        service.personal_artifacts(other)
    third = service.new_chat("guard-proposal-key", "Proposal key").session_id
    service.propose_profile(third, "p-1", 1, "Mira", created.appearance)
    _forge(service, third, "profile.proposed", "profile-proposal-p-9", _body_of(service, third, "profile.proposed"))
    with pytest.raises(HeinzelError, match="profile proposal is invalid"):
        service.latest_profile_proposal(third)


def test_schedule_review_and_issue_need_the_revision_the_person_saw(service):
    created = service.new_chat("guard-review", "Review")
    proposed = service.propose_check_in(created.session_id, "c-1", "UTC")
    arguments = {
        "session_id": created.session_id,
        "request_id": "approve-1",
        "action": "activate",
        "expected_revision": proposed.schedule.revision + 4,
        "expected_contract_version": 1,
        "expected_preview_hash": proposed.schedule.preview_hash,
    }
    with pytest.raises(HeinzelError, match="changed; reload before reviewing"):
        service.review("heinzel", "schedule-control", arguments)
    with pytest.raises(HeinzelError, match="changed; reload before approving"):
        service.issue("heinzel", "schedule-control", arguments, "test")
    assert service.applied("heinzel", "schedule-control", arguments) is False
    with pytest.raises(HeinzelError, match="invalid or unavailable"):
        service.review("heinzel", "schedule-control", {**arguments, "action": 3})
