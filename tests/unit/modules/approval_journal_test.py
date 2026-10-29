"""A private control journal consumes test-fixture decisions without exposing an issuer or an effect."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from heinzel import CallIdentity, CurrentUser, HeinzelError, Principal
from heinzel.kernel._approval import (
    ApprovalIntent,
    ApprovalRecord,
    approval_proof_hash,
    arguments_hash,
    consume_approval,
)
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
PROOF = "x" * 43


@pytest.fixture
def setup(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    repository = PersonalSessionRepository(current, clock=lambda: NOW)
    crud = SessionCrud(repository, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
    with current.local("cli"):
        session = crud.new_chat("new").session_id
        intent = ApprovalIntent(
            owner=current.principal().id,
            audience="schedule",
            session=session,
            action="activate",
            resource="schedule",
            arguments_hash=arguments_hash({"cron": "*/5 * * * *"}),
            contract_revision=1,
            grant_revision=2,
            policy_revision=3,
        )
        record = ApprovalRecord(
            intent=intent,
            proof_hash=approval_proof_hash(PROOF),
            nonce="f" * 64,
            provenance="test-only",
            decision="approve",
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
        # This is a synthetic fixture, not a human-approval issuer or a production operation.
        with repository.control_writer(session) as writer:
            writer.append("approval.issued", key=record.proof_hash, data=record.model_dump(mode="json"))
    return current, repository, session, intent, record


def consume(repository, session, intent, *, now=NOW):
    return consume_approval(repository, session, PROOF, intent, lambda _: intent, lambda: now)


def test_consumption_and_intent_are_one_durable_event_with_safe_replay(setup):
    current, repository, session, intent, record = setup
    with current.local("cli"):
        first = consume(repository, session, intent)
        assert first.disposition == "recorded"
        reopened = PersonalSessionRepository(current, clock=lambda: NOW)
        replay = consume(reopened, session, intent, now=NOW + timedelta(days=1))
        assert (replay.disposition, replay.sequence) == ("existing", first.sequence)
        events = reopened.control_read(session).events
        saved = [event for event in events if event.kind == "approval.intent"]
        assert len(saved) == 1
        assert saved[0].key == record.nonce
        assert saved[0].data["intent"] == intent.model_dump(mode="json")
        assert PROOF not in json.dumps([event.model_dump(mode="json") for event in events])
        with pytest.raises(HeinzelError, match="invalid or unavailable"):
            consume(repository, session, intent.model_copy(update={"action": "pause"}))


def test_expiry_revocation_and_current_revision_fail_before_append(setup):
    current, repository, session, intent, record = setup
    with current.local("cli"):
        with pytest.raises(HeinzelError, match="invalid or unavailable"):
            consume(repository, session, intent, now=NOW + timedelta(minutes=5))
        with pytest.raises(HeinzelError, match="invalid or unavailable"):
            consume_approval(
                repository,
                session,
                PROOF,
                intent,
                lambda _: intent.model_copy(update={"policy_revision": 4}),
                lambda: NOW,
            )
        with repository.control_writer(session) as writer:
            writer.append("approval.revoked", key=record.nonce)
        with pytest.raises(HeinzelError, match="invalid or unavailable"):
            consume(repository, session, intent)
        assert not any(event.kind == "approval.intent" for event in repository.control_read(session).events)


def test_concurrent_consumers_append_only_once(setup):
    current, repository, session, intent, _record = setup
    start = threading.Barrier(2)

    def worker():
        with current.local("cli"):
            start.wait(timeout=10)
            return consume(repository, session, intent)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: worker(), range(2)))
    assert {item.disposition for item in results} == {"recorded", "existing"}
    assert len({item.sequence for item in results}) == 1
    with current.local("cli"):
        assert sum(event.kind == "approval.intent" for event in repository.control_read(session).events) == 1


def test_unknown_acknowledgement_after_durable_append_recovers_as_existing(setup, monkeypatch):
    from heinzel.kernel._session_store import SessionWriter

    current, repository, session, intent, _record = setup
    original = SessionWriter.append
    interrupted = False

    def append_then_interrupt(writer, kind, **kwargs):
        nonlocal interrupted
        event = original(writer, kind, **kwargs)
        if kind == "approval.intent" and not interrupted:
            interrupted = True
            msg = "interrupted after durable append"
            raise OSError(msg)
        return event

    monkeypatch.setattr(SessionWriter, "append", append_then_interrupt)
    with current.local("cli"):
        with pytest.raises(OSError, match="interrupted"):
            consume(repository, session, intent)
        recovered = consume(PersonalSessionRepository(current), session, intent)
        assert recovered.disposition == "existing"
        assert sum(event.kind == "approval.intent" for event in repository.control_read(session).events) == 1


def test_revocation_recorded_while_consumer_waits_for_lock_wins(setup):
    current, repository, session, intent, record = setup
    started = threading.Event()

    def worker():
        with current.local("cli"):
            started.set()
            return consume(repository, session, intent)

    with current.local("cli"), ThreadPoolExecutor(max_workers=1) as executor:
        with repository.control_writer(session) as writer:
            pending = executor.submit(worker)
            assert started.wait(5)
            writer.append("approval.revoked", key=record.nonce)
        with pytest.raises(HeinzelError, match="invalid or unavailable"):
            pending.result(timeout=10)
        assert not any(event.kind == "approval.intent" for event in repository.control_read(session).events)


def test_other_principal_cannot_consume_an_owned_proof(setup):
    current, repository, session, intent, _record = setup
    other = CallIdentity(Principal("u-" + "b" * 32), "mcp-stdio", "local", "hz-local", frozenset())
    with current.using(other), pytest.raises(HeinzelError):
        consume(repository, session, intent)
    with current.local("cli"):
        assert not any(event.kind == "approval.intent" for event in repository.control_read(session).events)


def test_expiry_is_read_after_waiting_for_the_control_lock(setup):
    current, repository, session, intent, _record = setup
    started = threading.Event()
    now = [NOW]

    def worker():
        with current.local("cli"):
            started.set()
            return consume_approval(repository, session, PROOF, intent, lambda _: intent, lambda: now[0])

    with current.local("cli"), ThreadPoolExecutor(max_workers=1) as executor:
        with repository.control_writer(session):
            pending = executor.submit(worker)
            assert started.wait(5)
            now[0] = NOW + timedelta(minutes=5)
        with pytest.raises(HeinzelError, match="invalid or unavailable"):
            pending.result(timeout=10)
        assert not any(event.kind == "approval.intent" for event in repository.control_read(session).events)


def test_replay_refuses_type_changed_intent_even_when_python_values_compare_equal(setup):
    current, repository, session, intent, record = setup
    with current.local("cli"):
        changed = intent.model_dump(mode="json")
        changed["contract_revision"] = True
        with repository.control_writer(session) as writer:
            writer.append(
                "approval.intent",
                key=record.nonce,
                data={"proof_hash": record.proof_hash, "intent": changed, "provenance": record.provenance},
            )
        with pytest.raises(HeinzelError, match="invalid or unavailable"):
            consume(repository, session, intent)
