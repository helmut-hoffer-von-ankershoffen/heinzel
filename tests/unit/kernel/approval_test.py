"""A pure verifier binds stored decisions; it is not an issuer or a nonce ledger."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from pydantic import ValidationError

from heinzel.kernel._approval import ApprovalIntent, ApprovalRecord, arguments_hash, verify_approval
from heinzel.kernel._identity import Principal

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
PROOF = "x" * 43
OWNER = Principal("u-" + "a" * 32)


@pytest.fixture
def intent():
    return ApprovalIntent(
        owner=OWNER.id,
        audience="personal-schedule",
        session="session-1",
        action="activate",
        resource="schedule-1",
        arguments_hash=arguments_hash({"cron": "*/5 * * * *", "enabled": True}),
        contract_revision=1,
        grant_revision=2,
        policy_revision=3,
    )


@pytest.fixture
def record(intent):
    return ApprovalRecord(
        intent=intent,
        proof_hash=sha256(PROOF.encode()).hexdigest(),
        nonce="f" * 64,
        provenance="human-test-fixture",
        decision="approve",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )


def test_proof_matches_only_exact_trusted_intent(record, intent):
    evidence = verify_approval(PROOF, record, intent, OWNER, NOW, revoked=False)
    assert evidence is not None
    assert evidence.nonce == "f" * 64
    assert PROOF not in repr(evidence)
    assert verify_approval("z" * 43, record, intent, OWNER, NOW, revoked=False) is None
    assert verify_approval(PROOF, record, intent, Principal("u-" + "b" * 32), NOW, revoked=False) is None
    assert verify_approval(PROOF, record, intent, OWNER, NOW, revoked=True) is None
    wrong_type = intent.model_copy(update={"contract_revision": True})
    assert verify_approval(PROOF, record, wrong_type, OWNER, NOW, revoked=False) is None


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("owner", "u-" + "b" * 32),
        ("audience", "other"),
        ("session", "other"),
        ("action", "pause"),
        ("resource", "other"),
        ("arguments_hash", "a" * 64),
        ("contract_revision", 2),
        ("grant_revision", 3),
        ("policy_revision", 4),
    ],
)
def test_every_intent_binding_must_match(record, intent, field, changed):
    assert verify_approval(PROOF, record, intent.model_copy(update={field: changed}), OWNER, NOW, revoked=False) is None


def test_expiry_issue_time_and_proof_shape_are_fail_closed(record, intent):
    assert verify_approval(PROOF, record, intent, OWNER, NOW - timedelta(microseconds=1), revoked=False) is None
    assert verify_approval(PROOF, record, intent, OWNER, NOW + timedelta(minutes=5), revoked=False) is None
    assert verify_approval(PROOF, record, intent, OWNER, NOW.replace(tzinfo=None), revoked=False) is None
    assert verify_approval("x", record, intent, OWNER, NOW, revoked=False) is None
    assert verify_approval("é" * 43, record, intent, OWNER, NOW, revoked=False) is None
    invalid_period = record.model_copy(update={"expires_at": NOW})
    assert verify_approval(PROOF, invalid_period, intent, OWNER, NOW, revoked=False) is None


def test_canonical_arguments_preserve_json_types_and_are_order_independent():
    assert arguments_hash({"a": 1, "b": True}) == arguments_hash({"b": True, "a": 1})
    assert arguments_hash({"a": 1}) != arguments_hash({"a": True})
    assert arguments_hash({"a": 1}) != arguments_hash({"a": 1.0})
    assert arguments_hash({"a": "1"}) != arguments_hash({"a": 1})
    with pytest.raises(ValueError, match="finite JSON"):
        arguments_hash(float("nan"))
    with pytest.raises(ValueError, match="finite JSON"):
        arguments_hash({1: "not a JSON key"})
    with pytest.raises(ValueError, match="finite JSON"):
        arguments_hash((1, 2))


def test_record_is_immutable_and_rejects_unknown_fields(intent):
    with pytest.raises(ValidationError):
        intent.contract_revision = 3
    with pytest.raises(ValidationError):
        ApprovalIntent.model_validate(intent.model_dump() | {"extra": "authority"})
