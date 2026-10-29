"""Personal identity and inbox acceptance survive restart without granting a model or another owner authority."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from pydantic import ValidationError

from heinzel import CallIdentity, CurrentUser, HeinzelError, Principal
from heinzel.kernel._approval import ApprovalIntent, ApprovalRecord, approval_proof_hash, arguments_hash
from heinzel.modules.heinzel import _session_crud
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import HeinzelAppearance, MessageRequest, SessionCosts, SessionMessage
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit

AT = datetime(2026, 10, 25, tzinfo=UTC)


def _issue_test_approval(repository, current, session_id, request_id, revision, action, preview_hash):  # noqa: PLR0913, PLR0917
    proof = sha256(f"proof:{request_id}".encode()).hexdigest()
    intent = ApprovalIntent(
        owner=current.principal().id,
        audience="personal-check-in",
        session=session_id,
        action=action,
        resource="five-minute-check-in",
        arguments_hash=arguments_hash(
            {
                "request_id": request_id,
                "expected_revision": revision,
                "contract_version": 1,
                "preview_hash": preview_hash,
            }
        ),
        contract_revision=1,
        grant_revision=0,
        policy_revision=0,
    )
    record = ApprovalRecord(
        intent=intent,
        proof_hash=approval_proof_hash(proof),
        nonce=sha256(f"nonce:{request_id}".encode()).hexdigest(),
        provenance="test-only-synthetic-decision",
        decision="approve",
        issued_at=AT,
        expires_at=AT + timedelta(minutes=5),
    )
    with repository.control_writer(session_id) as writer:
        writer.append("approval.issued", key=record.proof_hash, data=record.model_dump(mode="json"))
    return proof


@pytest.fixture
def services(tmp_path):
    current = CurrentUser(tmp_path / "state" / "identity")
    repository = PersonalSessionRepository(current, clock=lambda: AT)
    service = SessionCrud(repository, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2), clock=lambda: AT)
    with current.local("cli"):
        yield service, repository, current


def test_creation_is_immediate_durable_and_idempotent_with_private_profile_bodies(services):
    service, repository, current = services
    look = HeinzelAppearance(hat="teal", beard="long", accessories=["scarf"])
    first = service.new_chat("create-1", "Personal helper", look)
    assert first.state == "proposed"
    assert first.messages == []
    assert first.session_id == first.heinzel_id
    assert first.appearance == look
    restarted = SessionCrud(PersonalSessionRepository(current), service.costs)
    assert restarted.new_chat("create-1", "Personal helper", look) == first
    assert [item.session_id for item in restarted.listing().sessions] == [first.session_id]
    metadata = json.dumps([event.model_dump(mode="json") for event in repository.control_read(first.session_id).events])
    assert "Personal helper" not in metadata
    assert "teal" not in metadata
    with pytest.raises(HeinzelError, match="different Heinzel details"):
        restarted.new_chat("create-1", "Changed helper", look)


def test_profile_exact_replay_precedes_stale_revision_and_restore_default_roundtrips(services):
    service, _repository, _current = services
    first = service.new_chat("create-1")
    changed = service.profile(
        first.session_id, "edit-1", 1, name="Named helper", appearance=HeinzelAppearance(hat="blue")
    )
    assert changed.profile_revision == 2
    assert (
        service.profile(first.session_id, "edit-1", 1, name="Named helper", appearance=HeinzelAppearance(hat="blue"))
        == changed
    )
    with pytest.raises(HeinzelError, match="different profile"):
        service.profile(first.session_id, "edit-1", 1, name="Other")
    with pytest.raises(HeinzelError, match="profile changed"):
        service.profile(first.session_id, "edit-2", 1, name="Other")
    restored = service.profile(first.session_id, "edit-3", 2, appearance=HeinzelAppearance())
    assert restored.appearance.hat is restored.appearance.shirt is restored.appearance.beard is None
    assert restored.name == "Named helper"


def test_check_in_contract_and_schedule_are_private_inactive_versioned_and_restartable(services):  # noqa: PLR0915
    service, repository, current = services
    created = service.new_chat("create-1", "Five-minute helper", HeinzelAppearance(hat="teal"))
    proposed = service.propose_check_in(created.session_id, "check-in-v1", "Europe/Berlin")
    assert proposed.schedule is not None
    assert proposed.schedule.status == "proposed"
    assert proposed.state == "proposed"
    assert proposed.schedule.cron == "*/5 * * * *"
    assert proposed.contract_version is None
    assert proposed.contracts[0].version == 1
    assert proposed.contracts[0].status == "proposed"
    assert proposed.contracts[0].text
    assert service.propose_check_in(created.session_id, "check-in-v1", "Europe/Berlin") == proposed

    with pytest.raises(HeinzelError, match="Approval is invalid"):
        service.schedule_control(
            created.session_id,
            "activate-v1",
            1,
            "activate",
            expected_contract_version=proposed.schedule.contract_version,
            expected_preview_hash=proposed.schedule.preview_hash,
        )

    spine = json.dumps([event.model_dump(mode="json") for event in repository.control_read(created.session_id).events])
    assert "Five-minute check-in" not in spine
    assert "At each explicitly approved" not in spine
    assert "Five-minute helper" not in spine

    activate_proof = _issue_test_approval(
        repository, current, created.session_id, "activate-v1", 1, "activate", proposed.schedule.preview_hash
    )
    with pytest.raises(HeinzelError, match="Approval is invalid"):
        service.schedule_control(
            created.session_id,
            "activate-v1",
            1,
            "activate",
            activate_proof,
            proposed.schedule.contract_version,
            "0" * 64,
        )
    active = service.schedule_control(
        created.session_id,
        "activate-v1",
        1,
        "activate",
        activate_proof,
        proposed.schedule.contract_version,
        proposed.schedule.preview_hash,
    )
    assert active.schedule is not None
    assert active.schedule.status == "active"
    assert active.contract_version == 1
    assert active.contracts[0].status == "approved"
    assert active.state == "awake"
    service.clock = lambda: AT + timedelta(days=1)
    assert (
        service.schedule_control(
            created.session_id,
            "activate-v1",
            1,
            "activate",
            activate_proof,
            proposed.schedule.contract_version,
            proposed.schedule.preview_hash,
        )
        == active
    )
    assert sum(event.kind == "schedule.activated" for event in repository.control_read(created.session_id).events) == 1
    service.clock = lambda: AT
    with pytest.raises(HeinzelError, match="different schedule control"):
        service.schedule_control(created.session_id, "activate-v1", 1, "pause")
    with pytest.raises(HeinzelError, match="schedule changed"):
        service.schedule_control(created.session_id, "stale-pause", 1, "pause")
    paused = service.schedule_control(created.session_id, "pause-v1", 2, "pause")
    assert paused.schedule is not None
    assert paused.schedule.status == "paused"
    assert paused.state == "paused"
    with pytest.raises(HeinzelError, match="Approval is invalid"):
        service.schedule_control(
            created.session_id,
            "resume-v1",
            3,
            "resume",
            expected_contract_version=proposed.schedule.contract_version,
            expected_preview_hash=proposed.schedule.preview_hash,
        )
    resume_proof = _issue_test_approval(
        repository, current, created.session_id, "resume-v1", 3, "resume", proposed.schedule.preview_hash
    )
    resumed = service.schedule_control(
        created.session_id,
        "resume-v1",
        3,
        "resume",
        resume_proof,
        proposed.schedule.contract_version,
        proposed.schedule.preview_hash,
    )
    assert resumed.schedule is not None
    assert resumed.schedule.status == "active"
    assert resumed.appearance == HeinzelAppearance(hat="teal")
    replayed_resume = service.schedule_control(
        created.session_id,
        "resume-v1",
        3,
        "resume",
        None,
        proposed.schedule.contract_version,
        proposed.schedule.preview_hash,
    )
    assert replayed_resume.schedule is not None
    assert replayed_resume.schedule.status == "active"
    with pytest.raises(HeinzelError, match="different schedule control"):
        service.schedule_control(
            created.session_id,
            "resume-v1",
            4,
            "resume",
            None,
            proposed.schedule.contract_version,
            proposed.schedule.preview_hash,
        )

    restarted = SessionCrud(PersonalSessionRepository(current), service.costs, clock=lambda: AT)
    reopened = restarted.snapshot(created.session_id)
    assert reopened.schedule == resumed.schedule
    assert reopened.contracts == resumed.contracts
    assert reopened.name == resumed.name
    assert restarted.listing().sessions[0].state == "awake"


def test_check_in_schedule_requires_valid_timezone_and_explicit_transition(services):
    service, _repository, _current = services
    created = service.new_chat("create-1")
    with pytest.raises(HeinzelError, match="Unknown IANA time zone"):
        service.propose_check_in(created.session_id, "check-in-v1", "Mars/Olympus")
    assert service.snapshot(created.session_id).schedule is None
    proposed = service.propose_check_in(created.session_id, "check-in-v1")
    assert proposed.schedule is not None
    assert proposed.schedule.status == "proposed"
    assert proposed.state == "proposed"
    with pytest.raises(HeinzelError, match="Cannot pause"):
        service.schedule_control(created.session_id, "premature-pause", 1, "pause")


def test_only_the_internal_issuer_records_a_fresh_exact_intent(services):
    service, repository, _current = services
    created = service.new_chat("issuer-create")
    proposed = service.propose_check_in(created.session_id, "issuer-check-in", "Europe/Berlin")
    assert proposed.schedule is not None
    arguments = {
        "session_id": created.session_id,
        "request_id": "issuer-activate",
        "expected_revision": proposed.schedule.revision,
        "action": "activate",
        "expected_contract_version": proposed.schedule.contract_version,
        "expected_preview_hash": proposed.schedule.preview_hash,
    }
    with pytest.raises(HeinzelError, match="invalid or unavailable"):
        service.issue("heinzel", "schedule-control", arguments | {"approval_proof": "caller-supplied"}, "test")
    with pytest.raises(HeinzelError, match="invalid or unavailable"):
        service.issue("other", "schedule-control", arguments, "test")
    proof = service.issue("heinzel", "schedule-control", arguments, "chat-confirm:test:a1")
    assert len(proof) >= 43
    events = repository.control_read(created.session_id).events
    issued = [event for event in events if event.kind == "approval.issued"]
    assert len(issued) == 1
    record = ApprovalRecord.model_validate_json(json.dumps(issued[0].data))
    assert record.intent.session == created.session_id
    assert record.intent.arguments_hash == arguments_hash(
        {
            "request_id": arguments["request_id"],
            "expected_revision": arguments["expected_revision"],
            "contract_version": arguments["expected_contract_version"],
            "preview_hash": arguments["expected_preview_hash"],
        }
    )
    assert record.provenance == "chat-confirm:test:a1"
    assert record.expires_at - record.issued_at == timedelta(minutes=5)
    active = service.schedule_control(
        created.session_id,
        arguments["request_id"],
        arguments["expected_revision"],
        "activate",
        proof,
        arguments["expected_contract_version"],
        arguments["expected_preview_hash"],
    )
    assert active.schedule is not None
    assert active.schedule.status == "active"
    replayed = service.schedule_control(
        created.session_id,
        arguments["request_id"],
        arguments["expected_revision"],
        "activate",
        None,
        arguments["expected_contract_version"],
        arguments["expected_preview_hash"],
    )
    assert replayed.schedule is not None
    assert replayed.schedule.status == "active"
    with pytest.raises(HeinzelError, match="different schedule control"):
        service.schedule_control(
            created.session_id,
            arguments["request_id"],
            arguments["expected_revision"] + 1,
            "activate",
            None,
            arguments["expected_contract_version"],
            arguments["expected_preview_hash"],
        )
    assert service.applied("heinzel", "schedule-control", arguments)
    assert not service.applied("heinzel", "schedule-control", arguments | {"request_id": "another-request"})


def test_identity_is_required_each_call_and_other_principal_cannot_read_or_change_a_known_id(services):
    service, repository, current = services
    first = service.new_chat("create-1")
    other = CallIdentity(
        Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:read", "private:write"})
    )
    with current.using(None), pytest.raises(HeinzelError, match="authenticated"):
        service.snapshot(first.session_id)
    with current.using(other):
        assert service.listing().sessions == []
        with pytest.raises(HeinzelError, match="No such personal"):
            service.snapshot(first.session_id)
        with pytest.raises(HeinzelError, match="No such personal"):
            service.accept(first.session_id, MessageRequest(request_id="m1", text="Hello"))
    assert repository.principal_id == current.owner().id
    assert service.snapshot(first.session_id) == first


def test_inbox_preserves_request_id_content_and_order_across_profile_gaps(services):
    service, repository, _current = services
    first = service.new_chat("create-1")
    one = service.accept(first.session_id, MessageRequest(request_id="m1", text="One"))
    service.profile(first.session_id, "edit", 1, name="Helper")
    two = service.accept(first.session_id, MessageRequest(request_id="m2", text="Two", when_busy="steer"))
    assert service.accept(first.session_id, MessageRequest(request_id="m1", text="One")) == one
    with pytest.raises(HeinzelError, match="different accepted"):
        service.accept(first.session_id, MessageRequest(request_id="m1", text="Changed"))
    page = service.snapshot(first.session_id, limit=1)
    assert page.has_more
    assert page.next_cursor
    assert [(message.id, message.text, message.seq) for message in page.messages] == [("m1", "One", one.inbox_position)]
    later = service.snapshot(first.session_id, cursor=page.next_cursor, limit=1)
    assert [(message.id, message.text, message.seq) for message in later.messages] == [
        ("m2", "Two", two.inbox_position)
    ]
    assert two.inbox_position == one.inbox_position + 2
    assert not later.has_more
    assert later.last_seq == repository.control_read(first.session_id).events[-1].seq
    other = service.new_chat("create-2")
    with pytest.raises(HeinzelError, match="cursor belongs"):
        service.snapshot(other.session_id, cursor=page.next_cursor)


def test_active_main_writer_does_not_block_snapshot_profile_or_inbox(services):
    service, repository, _current = services
    first = service.new_chat("create-1")
    with repository.main_writer(first.session_id):
        assert service.new_chat("create-1").session_id == first.session_id
        service.profile(first.session_id, "edit", 1, name="Changed while busy")
        service.accept(first.session_id, MessageRequest(request_id="m1", text="Follow up"))
        assert service.snapshot(first.session_id).name == "Changed while busy"
        assert len(service.snapshot(first.session_id).messages) == 1


def test_assistant_reply_uses_main_immutable_body_but_control_paging_order(services):
    service, repository, _current = services
    first = service.new_chat("create-1")
    service.accept(first.session_id, MessageRequest(request_id="m1", text="Hello"))
    with repository.main_writer(first.session_id) as main:
        body = immutable_body(
            main,
            "reply",
            SessionMessage(id="reply-t1", seq=1, turn_id="t1", role="assistant", text="Hi", at=AT).model_dump(
                mode="json"
            ),
        )
        with repository.control_writer(first.session_id) as control:
            event = control.append(
                "message.replied", key="reply-t1", data={"message_id": "reply-t1", **body.model_dump(mode="json")}
            )
    messages = service.snapshot(first.session_id).messages
    assert [(message.role, message.text) for message in messages] == [("user", "Hello"), ("assistant", "Hi")]
    assert messages[-1].seq == event.seq


@pytest.mark.parametrize("message_id", ["reply-other", "../outside", True])
def test_reply_checkpoint_cannot_replace_or_bypass_the_immutable_message_identity(services, message_id):
    service, repository, _current = services
    first = service.new_chat("create-1")
    with repository.main_writer(first.session_id) as main:
        body = immutable_body(
            main,
            "reply",
            SessionMessage(id="reply-t1", seq=1, turn_id="t1", role="assistant", text="Hi", at=AT).model_dump(
                mode="json"
            ),
        )
        with repository.control_writer(first.session_id) as control:
            control.append(
                "message.replied", key="reply-t1", data={"message_id": message_id, **body.model_dump(mode="json")}
            )
    with pytest.raises(HeinzelError, match="message identity"):
        service.snapshot(first.session_id)


def test_unreferenced_body_after_crash_cannot_change_committed_profile_hash(services):
    service, repository, _current = services
    first = service.new_chat("create-1")
    with repository.control_writer(first.session_id) as writer:
        orphan = immutable_body(writer, "profile", {"name": "Uncommitted", "appearance": {}}, control=True)
    assert service.snapshot(first.session_id) == first
    assert orphan.body_id not in json.dumps(repository.control_read(first.session_id).model_dump(mode="json"))


@pytest.mark.parametrize("boundary", ["reservation", "profile-body"])
def test_original_creation_request_repairs_crash_before_initial_profile_commit(services, monkeypatch, boundary):
    service, repository, _current = services
    if boundary == "reservation":
        digest = hashlib.sha256(
            json.dumps(
                {"name": "New Heinzel", "appearance": HeinzelAppearance().model_dump(mode="json")},
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        reserved_id = repository.reserve("create-1", digest)
    else:
        save = _session_crud.immutable_body

        def interrupted(*args, **kwargs):
            save(*args, **kwargs)
            msg = "crash after snapshot fsync"
            raise InterruptedError(msg)

        with monkeypatch.context() as patch:
            patch.setattr(_session_crud, "immutable_body", interrupted)
            with pytest.raises(InterruptedError, match="after snapshot"):
                service.new_chat("create-1")
        reserved_id = repository.heads()[0].session
    repaired = service.new_chat("create-1")
    assert repaired.session_id == reserved_id
    assert repaired.name == "New Heinzel"
    assert repaired.profile_revision == 1
    assert len(repository.main_read(reserved_id).events) == 1
    assert len(repository.control_read(reserved_id).events) == 1


def test_message_expiry_is_explicit_and_edited_valid_json_is_refused(services):
    service, repository, current = services
    first = service.new_chat("create-1")
    service.accept(first.session_id, MessageRequest(request_id="m1", text="Hello"))
    event = repository.control_read(first.session_id).events[-1]
    path = (
        current.directory.parent
        / "heinzel-sessions"
        / current.principal().id
        / "control"
        / first.session_id
        / "turns"
        / f"{event.data['body_id']}.jsonl"
    )
    original = path.read_bytes()
    path.write_bytes(original.replace(b"Hello", b"Edited"))
    assert hashlib.sha256(path.read_bytes()).hexdigest() != event.data["body_hash"]
    with pytest.raises(HeinzelError, match="saved checkpoint"):
        service.snapshot(first.session_id)
    path.unlink()
    assert service.snapshot(first.session_id).messages[0].status == "expired"


@pytest.mark.parametrize("name", ["", "  ", "x" * 101])
def test_invalid_names_do_not_reserve_a_session(services, name):
    service, _repository, _current = services
    with pytest.raises(ValidationError):
        service.new_chat("create-1", name)
    assert service.listing().sessions == []


def test_scheduled_agent_proposal_is_inert_private_idempotent_and_separate_from_check_in(services):
    service, repository, current = services
    session = service.new_chat("create-agent-preview")
    check_in = service.propose_check_in(session.session_id, "notice-only")
    proposal = service.propose_scheduled_agent(session.session_id, "agent-1", "Summarize local context", 0.2, 60)
    assert proposal.status == "proposed"
    assert proposal.contract == "Summarize local context"
    assert proposal.permitted_tools == []
    assert proposal.max_usd == 0.2
    assert proposal.timeout_seconds == 60
    assert len(proposal.preview_sha256) == 64
    after = service.snapshot(session.session_id)
    assert after.schedule == check_in.schedule
    assert after.contracts == check_in.contracts
    restarted = SessionCrud(PersonalSessionRepository(current), service.costs)
    assert restarted.scheduled_agent_proposal(session.session_id, "agent-1") == proposal
    assert (
        restarted.propose_scheduled_agent(session.session_id, "agent-1", "Summarize local context", 0.2, 60) == proposal
    )
    with pytest.raises(HeinzelError, match="different agent execution proposal"):
        service.propose_scheduled_agent(session.session_id, "agent-1", "Different instructions", 0.2, 60)
    metadata = json.dumps(
        [event.model_dump(mode="json") for event in repository.control_read(session.session_id).events]
    )
    assert "Summarize local context" not in metadata
    assert "approval.issued" not in metadata


@pytest.mark.parametrize(
    ("contract", "budget", "timeout"), [("Other", 0.2, 60), ("Instructions", 0.3, 60), ("Instructions", 0.2, 90)]
)
def test_scheduled_agent_proposal_rejects_reusing_identity_for_changed_terms(services, contract, budget, timeout):
    service, _, _ = services
    session_id = service.new_chat("proposal-owner").session_id
    original = service.propose_scheduled_agent(session_id, "proposal-1", "Instructions", 0.2, 60)
    with pytest.raises(HeinzelError, match="different agent execution proposal"):
        service.propose_scheduled_agent(session_id, "proposal-1", contract, budget, timeout)
    assert service.scheduled_agent_proposal(session_id, "proposal-1") == original


def test_scheduled_agent_proposal_checks_owner_and_session(services):
    service, _, current = services
    one = service.new_chat("agent-owner-1").session_id
    two = service.new_chat("agent-owner-2").session_id
    proposal = service.propose_scheduled_agent(one, "same-request", "Instructions", 0.2, 60)
    other = service.propose_scheduled_agent(two, "same-request", "Instructions", 0.2, 60)
    assert proposal.preview_sha256 != other.preview_sha256
    stranger = CallIdentity(
        Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:read", "private:write"})
    )
    with current.using(stranger), pytest.raises(HeinzelError, match="No such personal"):
        service.scheduled_agent_proposal(one, "same-request")
    with current.using(stranger), pytest.raises(HeinzelError, match="No such personal"):
        service.propose_scheduled_agent(one, "different", "Instructions", 0.2, 60)


def test_scheduled_agent_proposal_rejects_invalid_terms(services):
    service, _, _ = services
    session_id = service.new_chat("invalid-agent").session_id
    with pytest.raises(ValidationError):
        service.propose_scheduled_agent(session_id, "invalid", "Instructions", float("nan"), 60)
    with pytest.raises(ValidationError):
        service.propose_scheduled_agent(session_id, "invalid", "Instructions", 0.2, 301)
    with pytest.raises(HeinzelError, match="does not exist"):
        service.scheduled_agent_proposal(session_id, "invalid")


def test_scheduled_agent_proposal_rejects_inconsistent_stored_preview(services):
    service, repository, _ = services
    session_id = service.new_chat("invalid-preview-owner").session_id
    original = service.propose_scheduled_agent(session_id, "original", "Instructions", 0.2, 60)
    forged = original.model_copy(update={"request_id": "forged"})
    with repository.control_writer(session_id) as writer:
        body = immutable_body(writer, "agent-proposal", forged.model_dump(mode="json"), control=True)
        writer.append("agent.proposed", key="agent-proposal-forged", data=body.model_dump(mode="json"))
    with pytest.raises(HeinzelError, match="proposal is invalid"):
        service.scheduled_agent_proposal(session_id, "forged")
