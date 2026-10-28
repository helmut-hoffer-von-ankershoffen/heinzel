"""Synthetic approval consumption serializes across separate authenticated local processes."""

import multiprocessing
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from heinzel import CurrentUser
from heinzel.kernel._approval import ApprovalIntent, ApprovalRecord, arguments_hash, consume_approval
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
PROOF = "x" * 43


def consume_process(root, session, encoded_intent, start, results):
    current = CurrentUser(Path(root) / "identity")
    repository = PersonalSessionRepository(current, clock=lambda: NOW)
    intent = ApprovalIntent.model_validate_json(encoded_intent)
    with current.local("mcp-stdio"):
        assert start.wait(10)
        result = consume_approval(repository, session, PROOF, intent, lambda _: intent, lambda: NOW)
        results.put((result.disposition, result.sequence))


def test_multiple_processes_cannot_consume_one_nonce_twice(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
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
            proof_hash=sha256(PROOF.encode()).hexdigest(),
            nonce="f" * 64,
            provenance="synthetic-test",
            decision="approve",
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
        # No production issuer is exposed: this only seeds a synthetic test record.
        with repository.control_writer(session) as writer:
            writer.append("approval.issued", key=record.proof_hash, data=record.model_dump(mode="json"))
    context = multiprocessing.get_context("spawn")
    start, results = context.Event(), context.Queue()
    processes = [
        context.Process(target=consume_process, args=(str(tmp_path), session, intent.model_dump_json(), start, results))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    try:
        start.set()
        outcomes = [results.get(timeout=15) for _ in processes]
    finally:
        start.set()
        for process in processes:
            process.join(10)
            if process.is_alive():
                process.terminate()
                process.join(10)
    assert all(process.exitcode == 0 for process in processes)
    assert {disposition for disposition, _ in outcomes} == {"recorded", "existing"}
    assert len({sequence for _, sequence in outcomes}) == 1
    with current.local("cli"):
        assert sum(event.kind == "approval.intent" for event in repository.control_read(session).events) == 1
