"""Session CRUD refuses impossible inputs and ambiguous history with a named error, never a silent guess."""

import base64
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from heinzel import CurrentUser, HeinzelError, SessionEvent
from heinzel.modules.heinzel import _session_crud as crud
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import ContractVersion, HeinzelAppearance, MessageRequest, SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit

AT = datetime(2026, 10, 25, tzinfo=UTC)
NAIVE = datetime(2026, 10, 25)  # noqa: DTZ001 - the checkpoint must refuse a naive time


@pytest.fixture
def services(tmp_path):
    current = CurrentUser(tmp_path / "state" / "identity")
    repository = PersonalSessionRepository(current, clock=lambda: AT)
    service = SessionCrud(repository, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2), clock=lambda: AT)
    with current.local("cli"):
        yield service


def _intent(slot: datetime = AT, key: str = "notice-1") -> crud._CheckInIntent:
    return crud._CheckInIntent(
        slot=slot,
        schedule_revision=1,
        contract_version=1,
        notice_key=key,
        title="Checked in",
        body="Due.",
        route="/heinzel",
        command="hz heinzel sessions",
    )


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"last_completed": NAIVE}, "watermark must include a timezone"),
        ({"pending": None, "last_completed": AT, "cleanup_key": None, "naive_pending": True}, "slot must include"),
        ({"pending": "intent", "last_completed": AT + timedelta(minutes=5)}, "cannot precede"),
        ({"cleanup_key": "k"}, "receipt cleanup requires"),
    ],
)
def test_impossible_check_in_checkpoints_are_refused(fields, message):
    values = dict(fields)
    if values.pop("naive_pending", False):
        values["pending"] = _intent().model_copy(update={"slot": NAIVE})
    elif values.get("pending") == "intent":
        values["pending"] = _intent()
    with pytest.raises(ValidationError, match=message):
        crud._CheckInCheckpoint(version=1, **values)


def _legacy(seq: int, kind: str, key: str | None, data: dict) -> SessionEvent:
    return SessionEvent(seq=seq, ts=AT, prev=None, kind=kind, key=key, data=data)


def _intent_data(slot: str = "2026-10-25T00:00:00+00:00", notice: str = "notice-1") -> dict:
    return {
        "dedup_version": 1,
        "slot": slot,
        "contract_version": 1,
        "notice_key": notice,
        "title": "Checked in",
        "body": "Due.",
        "route": "/heinzel",
        "command": "hz heinzel sessions",
    }


@pytest.mark.parametrize(
    ("events", "message"),
    [
        ([_legacy(1, "check-in.intent", "a", {**_intent_data(), "title": 3})], "invalid field types"),
        ([_legacy(1, "check-in.intent", "a", {**_intent_data(), "dedup_version": 2})], "ambiguous"),
        ([_legacy(1, "check-in.intent", "a", {**_intent_data(), "slot": 5})], "invalid field types"),
        ([_legacy(1, "check-in.intent", "a", _intent_data(slot="not a time"))], "ambiguous"),
        ([_legacy(1, "check-in.recorded", None, {})], "no identity"),
        ([_legacy(1, "check-in.recorded", "a", {"notice_key": "notice-1"})], "no matching intent"),
        (
            [
                _legacy(1, "check-in.intent", "a", _intent_data()),
                _legacy(2, "check-in.recorded", "a", {"notice_key": "other"}),
            ],
            "does not match its intent",
        ),
        (
            [
                _legacy(1, "check-in.intent", "a", _intent_data()),
                _legacy(2, "check-in.intent", "b", _intent_data(notice="notice-2")),
            ],
            "Several legacy check-in intents",
        ),
    ],
)
def test_ambiguous_legacy_check_in_history_is_refused(events, message):
    with pytest.raises(HeinzelError, match=message):
        crud._reconcile_legacy_check_in_events(crud._CheckInCheckpoint(version=1), events, 9, 1)


def test_a_legacy_completion_cannot_overlap_a_different_pending_intent():
    checkpoint = crud._CheckInCheckpoint(version=1, pending=_intent(AT + timedelta(hours=1), "notice-9"))
    events = [
        _legacy(1, "check-in.intent", "a", _intent_data()),
        _legacy(2, "check-in.recorded", "a", {"notice_key": "notice-1"}),
    ]
    with pytest.raises(HeinzelError, match="overlaps an unresolved"):
        crud._reconcile_legacy_check_in_events(checkpoint, events, 9, 1)


def test_a_legacy_intent_cannot_overlap_the_bounded_outbox():
    checkpoint = crud._CheckInCheckpoint(version=1, pending=_intent(AT + timedelta(hours=1), "notice-9"))
    with pytest.raises(HeinzelError, match="overlaps the bounded outbox"):
        crud._reconcile_legacy_check_in_events(checkpoint, [_legacy(1, "check-in.intent", "a", _intent_data())], 9, 1)


def test_a_legacy_intent_cannot_predate_its_watermark():
    checkpoint = crud._CheckInCheckpoint(version=1, last_completed=AT + timedelta(hours=1))
    with pytest.raises(HeinzelError, match="predates its completion watermark"):
        crud._reconcile_legacy_check_in_events(checkpoint, [_legacy(1, "check-in.intent", "a", _intent_data())], 9, 1)


def test_a_saved_schedule_must_be_the_check_in_and_name_a_known_contract():
    contract = ContractVersion(
        id="contract-1",
        version=1,
        sha256="0" * 64,
        status="proposed",
        title="Check-in",
        text="Check in.",
        created_at=AT,
    )
    state = crud._ScheduleState(
        contract_version=1, cron=crud.CHECK_IN_CRON, timezone="UTC", status="proposed", preview_hash="0" * 64
    )
    with pytest.raises(ValueError, match=r"^$"):
        crud._validate_schedule_state(state.model_copy(update={"cron": "0 * * * *"}), [contract])
    with pytest.raises(ValueError, match=r"^$"):
        crud._validate_schedule_state(state.model_copy(update={"contract_version": 2}), [contract])
    assert crud._validate_schedule_state(state, [contract]) == state


def test_public_inputs_out_of_range_are_refused(services):
    service = services
    session = service.new_chat("create-1").session_id
    with pytest.raises(HeinzelError, match="Attachments need a retained upload"):
        service.accept(session, MessageRequest(request_id="m1", text="hi", attachment_ids=["upload-1"]))
    with pytest.raises(HeinzelError, match="Choose between 1 and 200"):
        service.snapshot(session, limit=0)
    with pytest.raises(HeinzelError, match="Choose between 1 and"):
        service.recent_history(session, limit=0)
    ahead = crud._Cursor(v=1, session_id=session, after=999).model_dump_json().encode()
    with pytest.raises(HeinzelError, match="cursor is ahead"):
        service.snapshot(session, cursor=base64.urlsafe_b64encode(ahead).decode().rstrip("="))
    with pytest.raises(HeinzelError, match="Choose an IANA time zone"):
        service.propose_check_in(session, "check-in-1", " ")
    with pytest.raises(HeinzelError, match="search message position"):
        crud.history_cursor_before(service, session, 0, 1, "0" * 64)


@pytest.mark.parametrize(
    ("revision", "action", "contract", "message"),
    [
        (1, "explode", 1, "Choose activate, pause or resume"),
        (1, "pause", 0, "Reload the schedule"),
        (0, "pause", 1, "Reload the schedule"),
    ],
)
def test_schedule_control_refuses_unknown_actions_and_stale_revisions(services, revision, action, contract, message):
    service = services
    session = service.new_chat("create-1").session_id
    with pytest.raises(HeinzelError, match=message):
        service.schedule_control(session, "control-1", revision, action, expected_contract_version=contract)


def test_skill_reads_refuse_unknown_ids_and_bad_pages(services):
    service = services
    session = service.new_chat("create-1").session_id
    with pytest.raises(HeinzelError, match="sharing proposal does not exist"):
        service.skill_sharing(session, "missing-1")
    with pytest.raises(HeinzelError, match="Invalid sharing proposal page"):
        service.skill_sharing_proposals(session, -1)
    with pytest.raises(HeinzelError, match="Invalid draft cursor"):
        service.skill_drafts(session, -1)


def test_profile_proposals_are_idempotent_and_refuse_stale_or_changed_terms(services):
    service = services
    session = service.new_chat("create-1").session_id
    look = HeinzelAppearance(hat="teal")
    first = service.propose_profile(session, "look-1", 1, "Helper", look)
    assert service.propose_profile(session, "look-1", 1, "Helper", look) == first
    assert service.latest_profile_proposal(session) == first
    with pytest.raises(HeinzelError, match="already identifies different details"):
        service.propose_profile(session, "look-1", 1, "Other", look)
    with pytest.raises(HeinzelError, match="The profile changed"):
        service.propose_profile(session, "look-2", 7, "Helper", look)


def test_a_check_in_proposal_is_one_per_heinzel_and_exact_per_request(services):
    service = services
    session = service.new_chat("create-1").session_id
    proposed = service.propose_check_in(session, "check-in-1", "Europe/Berlin")
    assert proposed.schedule is not None
    with pytest.raises(HeinzelError, match="different contract proposal"):
        service.propose_check_in(session, "check-in-1", "UTC")
    with pytest.raises(HeinzelError, match="one immutable check-in contract"):
        service.propose_check_in(session, "check-in-2", "Europe/Berlin")
    with pytest.raises(HeinzelError, match="Reload the agent execution proposal"):
        service.amend_scheduled_agent(session, "agent-1", 0, "Do it.", None, 60)
    with pytest.raises(HeinzelError, match="schedule changed"):
        service.schedule_control(
            session,
            "pause-1",
            99,
            "pause",
            expected_contract_version=proposed.schedule.contract_version,
            expected_preview_hash=proposed.schedule.preview_hash,
        )


def test_damaged_private_records_are_refused_by_name(services):
    service = services
    session = service.new_chat("create-1").session_id
    with service.repository.control_writer(session) as writer:
        writer.append("artifact.associated", key="artifact-association-x", data={})
    with pytest.raises(HeinzelError, match="invalid private body reference"):
        service.personal_artifacts(session)

    other = service.new_chat("create-2").session_id
    with service.repository.control_writer(other) as writer:
        body = immutable_body(writer, "artifact", {"unexpected": True}, control=True)
        writer.append("artifact.associated", key="artifact-association-y", data=body.model_dump(mode="json"))
    with pytest.raises(HeinzelError, match="artifact association is invalid"):
        service.personal_artifacts(other)

    third = service.new_chat("create-3").session_id
    with service.repository.control_writer(third) as writer:
        body = immutable_body(writer, "profile", {"unexpected": True}, control=True)
        writer.append("profile.updated", key="edit-x", data={**body.model_dump(mode="json"), "revision": 2})
    with pytest.raises(HeinzelError, match="saved profile is invalid"):
        service.snapshot(third)
