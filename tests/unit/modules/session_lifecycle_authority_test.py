"""A retire pauses every recurring authority: a restore never resumes one without its own approval (slice 7 review)."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from heinzel import CurrentUser, HeinzelError
from heinzel.kernel._approval import ApprovalIntent, ApprovalRecord, approval_proof_hash, arguments_hash
from heinzel.kernel._module import OperationLet
from heinzel.modules.heinzel import module as heinzel_module
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_operations import _lifecycle_origin
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit
NOW = datetime(2026, 10, 26, 7, 0, tzinfo=UTC)


def _approve(sessions, current, session_id, request_id, revision, action, preview_hash):  # noqa: PLR0913, PLR0917
    proof = hashlib.sha256(f"proof:{request_id}".encode()).hexdigest()
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
        nonce=hashlib.sha256(f"n:{request_id}".encode()).hexdigest(),
        provenance="probe",
        decision="approve",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    with sessions.repository.control_writer(session_id) as writer:
        writer.append("approval.issued", key=record.proof_hash, data=record.model_dump(mode="json"))
    return proof


@pytest.fixture
def active(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    now = [NOW]
    crud = SessionCrud(
        PersonalSessionRepository(current, clock=lambda: now[0]),
        lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2),
        clock=lambda: now[0],
    )
    with current.local("cli"):
        created = crud.new_chat("probe-create", "Probe")
        proposed = crud.propose_check_in(created.session_id, "probe-proposal", "UTC")
        assert proposed.schedule is not None
        proof = _approve(
            crud, current, created.session_id, "probe-activate", 1, "activate", proposed.schedule.preview_hash
        )
        crud.schedule_control(
            created.session_id,
            "probe-activate",
            1,
            "activate",
            proof,
            proposed.schedule.contract_version,
            proposed.schedule.preview_hash,
        )
        yield crud, created.session_id, now


def test_pause_then_resume_needs_an_approval_proof(active):
    crud, session, _ = active
    schedule = crud.snapshot(session).schedule
    assert schedule is not None
    paused = crud.schedule_control(session, "probe-pause", schedule.revision, "pause")
    assert paused.schedule is not None
    with pytest.raises(HeinzelError, match="Approval"):
        crud.schedule_control(
            session, "probe-resume", paused.schedule.revision, "resume", None, 1, paused.schedule.preview_hash
        )


def test_retire_then_restore_does_not_resume_a_schedule_without_a_new_approval(active):
    crud, session, now = active
    crud.retire(session, "probe-retire")
    now[0] = NOW + timedelta(minutes=7)
    restored = crud.retire(session, "probe-restore", retired=False)
    assert restored.schedule is not None
    assert (restored.state, restored.schedule.status) == ("paused", "paused")
    with pytest.raises(HeinzelError, match="Approval"):
        crud.schedule_control(
            session, "probe-resume", restored.schedule.revision, "resume", None, 1, restored.schedule.preview_hash
        )


def test_a_restore_of_a_heinzel_that_is_not_retired_records_nothing(active):
    crud, session, _ = active
    before = len(crud.repository.control_read(session).events)
    crud.retire(session, "probe-noop-restore", retired=False)
    assert len(crud.repository.control_read(session).events) == before


def _op(name: str) -> OperationLet:
    return next(let for let in heinzel_module._lets if isinstance(let, OperationLet) and let.name == name)


@pytest.mark.parametrize("name", ["retire", "restore"])
def test_retire_and_restore_are_not_tools_of_an_mcp_client(name):
    heads = _op(name).heads
    assert heads == {"cli", "web", "api-private"}


def test_only_the_owner_through_a_human_facing_head_retires_or_restores(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        assert _lifecycle_origin(current) == "cli"
    for head in ("mcp-stdio", "daemon"):
        with current.local(head), pytest.raises(HeinzelError, match="requires its owner"):
            _lifecycle_origin(current)
