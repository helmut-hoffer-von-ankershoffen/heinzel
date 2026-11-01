"""Fake conditional writes require exact human confirmation and never replay uncertain attempts."""

import hashlib
import threading

import pytest

from heinzel import CurrentUser, HeinzelError
from heinzel.kernel._approval import ApprovalConfirmation
from heinzel.kernel._captured_write import (
    CapturedWriteAcknowledgement,
    CapturedWriteDraft,
    CapturedWriteLease,
    CapturedWriteReview,
    CapturedWriteService,
)
from heinzel.modules.heinzel._captured_write_host import CapturedWriteHost, review_payload
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit


class FakeProvider(CapturedWriteService):
    def __init__(self):
        self.revision = "one"
        self.account = "account-one"
        self.source = "source-one"
        self.scope = "fixture-write"
        self.writes = 0
        self.lose_ack = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = False
        self.change_account = False
        self.cas_race = False
        self.before_write = lambda: None

    def open_lease(self, draft):
        provider = self

        class Lease(CapturedWriteLease):
            def review(self):
                return CapturedWriteReview(
                    owner=draft.owner,
                    session=draft.session,
                    provider=draft.provider,
                    binding_id=draft.binding_id,
                    account=provider.account,
                    source=provider.source,
                    reference=draft.reference,
                    operation=draft.operation,
                    section=draft.section,
                    base_revision=provider.revision,
                    content_sha256=hashlib.sha256(draft.content.get_secret_value().encode()).hexdigest(),
                    write_scope=provider.scope,
                    conditional_mutation=True,
                )

            def write(self):
                provider.before_write()
                provider.entered.set()
                if provider.block:
                    assert provider.release.wait(5)
                if provider.cas_race:
                    provider.revision = "changed-at-commit"
                if provider.revision != draft.base_revision:
                    msg = "stale revision"
                    raise ValueError(msg)
                provider.writes += 1
                provider.revision = "two"
                if provider.change_account:
                    provider.account = "changed-after-write"
                if provider.lose_ack:
                    msg = "untrusted provider detail"
                    raise OSError(msg)
                return CapturedWriteAcknowledgement(reference=draft.reference, revision=provider.revision)

            def close(self):
                pass

        return Lease()


def _setup(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    context = current.local("cli")
    context.__enter__()
    crud = SessionCrud(PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
    session = crud.new_chat("new").session_id
    draft = CapturedWriteDraft.model_validate(
        {
            "owner": current.principal().id,
            "session": session,
            "request_id": "write-one",
            "provider": "fixture",
            "binding_id": "a" * 64,
            "account": "account-one",
            "source": "source-one",
            "reference": "fixture://document/one",
            "operation": "replace-section",
            "section": "summary",
            "base_revision": "one",
            "write_scope": "fixture-write",
            "content": "private replacement",
        }
    )
    provider = FakeProvider()
    return context, current, crud, draft, provider, CapturedWriteHost(crud, current, provider)


def _confirm(host, draft, proof):
    marker = ApprovalConfirmation()
    args = {
        "session_id": draft.session,
        "request_id": draft.request_id,
        "review": review_payload(draft),
        "approval_proof": proof,
    }
    with marker.using("heinzel", "execute-captured-write", args):
        return host.execute(draft.session, draft.request_id, review_payload(draft), proof, marker)


def test_exact_human_decision_records_attempt_before_one_acknowledged_write(tmp_path):
    context, _current, crud, draft, provider, host = _setup(tmp_path)
    try:
        host.draft(draft)
        assert provider.writes == 0
        assert host.status(draft.session, draft.request_id) == "draft"
        proof = host.issue(draft.session, draft.request_id, "human-test")

        def before_write():
            assert any(event.kind == "write.attempted" for event in crud.repository.control_read(draft.session).events)

        provider.before_write = before_write
        with pytest.raises(HeinzelError):
            host.execute(draft.session, draft.request_id, review_payload(draft), proof, ApprovalConfirmation())
        wrong = review_payload(draft) | {"content": "different content"}
        marker = ApprovalConfirmation()
        args = {"session_id": draft.session, "request_id": draft.request_id, "review": wrong, "approval_proof": proof}
        with marker.using("heinzel", "execute-captured-write", args), pytest.raises(HeinzelError):
            host.execute(draft.session, draft.request_id, wrong, proof, marker)
        assert provider.writes == 0
        assert _confirm(host, draft, proof) == "acknowledged"
        events = crud.repository.control_read(draft.session).events
        attempt = next(event for event in events if event.kind == "write.attempted")
        receipt = next(event for event in events if event.kind == "write.acknowledged")
        assert attempt.seq < receipt.seq
        assert receipt.data["attempt_seq"] == attempt.seq
        assert receipt.data["revision"] == "two"
        assert provider.writes == 1
        reopened = SessionCrud(
            PersonalSessionRepository(_current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        assert CapturedWriteHost(reopened, _current, provider).status(draft.session, draft.request_id) == "acknowledged"
        with pytest.raises(HeinzelError):
            _confirm(host, draft, proof)
        assert provider.writes == 1
    finally:
        context.__exit__(None, None, None)


def test_changed_account_and_lost_ack_never_replay(tmp_path):
    context, _current, crud, draft, provider, host = _setup(tmp_path)
    try:
        host.draft(draft)
        with pytest.raises(HeinzelError):
            host.draft(draft.model_copy(update={"account": "another"}))
        proof = host.issue(draft.session, draft.request_id, "human-test")
        provider.account = "changed"
        with pytest.raises(HeinzelError, match="uncertain"):
            _confirm(host, draft, proof)
        assert provider.writes == 0
        assert [event.kind for event in crud.repository.control_read(draft.session).events].count(
            "write.attempted"
        ) == 1
        with pytest.raises(HeinzelError):
            _confirm(host, draft, proof)
    finally:
        context.__exit__(None, None, None)


def test_revision_drift_denies_before_provider_write(tmp_path):
    context, _current, _crud, draft, provider, host = _setup(tmp_path)
    try:
        host.draft(draft)
        proof = host.issue(draft.session, draft.request_id, "human-test")
        provider.revision = "changed"
        with pytest.raises(HeinzelError, match="uncertain"):
            _confirm(host, draft, proof)
        assert provider.writes == 0
        assert host.status(draft.session, draft.request_id) == "uncertain"
    finally:
        context.__exit__(None, None, None)


def test_provider_compare_and_set_refuses_revision_change_at_mutation(tmp_path):
    context, _current, _crud, draft, provider, host = _setup(tmp_path)
    try:
        host.draft(draft)
        proof = host.issue(draft.session, draft.request_id, "human-test")
        provider.cas_race = True
        with pytest.raises(HeinzelError, match="uncertain"):
            _confirm(host, draft, proof)
        assert provider.writes == 0
        assert host.status(draft.session, draft.request_id) == "uncertain"
    finally:
        context.__exit__(None, None, None)


@pytest.mark.parametrize(("field", "value"), [("source", "changed"), ("scope", "read-only")])
def test_source_or_scope_drift_denies_before_provider_write(tmp_path, field, value):
    context, _current, _crud, draft, provider, host = _setup(tmp_path)
    try:
        host.draft(draft)
        proof = host.issue(draft.session, draft.request_id, "human-test")
        setattr(provider, field, value)
        with pytest.raises(HeinzelError, match="uncertain"):
            _confirm(host, draft, proof)
        assert provider.writes == 0
        assert host.status(draft.session, draft.request_id) == "uncertain"
    finally:
        context.__exit__(None, None, None)


def test_lost_provider_ack_is_not_a_success_or_second_write(tmp_path):
    context, _current, crud, draft, provider, host = _setup(tmp_path)
    try:
        host.draft(draft)
        proof = host.issue(draft.session, draft.request_id, "human-test")
        provider.lose_ack = True
        with pytest.raises(HeinzelError, match="uncertain"):
            _confirm(host, draft, proof)
        events = crud.repository.control_read(draft.session).events
        assert any(event.kind == "write.attempted" for event in events)
        assert not any(event.kind == "write.acknowledged" for event in events)
        assert host.status(draft.session, draft.request_id) == "uncertain"
        with pytest.raises(HeinzelError):
            _confirm(host, draft, proof)
        assert provider.writes == 1
    finally:
        context.__exit__(None, None, None)


def test_account_rotation_during_provider_write_never_records_receipt(tmp_path):
    context, _current, crud, draft, provider, host = _setup(tmp_path)
    try:
        host.draft(draft)
        proof = host.issue(draft.session, draft.request_id, "human-test")
        provider.change_account = True
        with pytest.raises(HeinzelError, match="uncertain"):
            _confirm(host, draft, proof)
        assert provider.writes == 1
        assert not any(
            event.kind == "write.acknowledged" for event in crud.repository.control_read(draft.session).events
        )
    finally:
        context.__exit__(None, None, None)


def test_main_writer_remains_held_until_fake_write_returns(tmp_path):
    context, current, crud, draft, provider, host = _setup(tmp_path)
    worker = None
    try:
        host.draft(draft)
        proof = host.issue(draft.session, draft.request_id, "human-test")
        provider.block = True
        results = []

        def write():
            with current.local("cli"):
                results.append(_confirm(host, draft, proof))

        worker = threading.Thread(target=write)
        worker.start()
        assert provider.entered.wait(5)
        with pytest.raises(HeinzelError), crud.repository.main_writer(draft.session):
            pytest.fail("A second writer entered during the provider call")
        provider.release.set()
        worker.join(5)
        assert not worker.is_alive()
        assert results == ["acknowledged"]
        with crud.repository.main_writer(draft.session):
            pass
    finally:
        provider.release.set()
        if worker:
            worker.join(5)
        context.__exit__(None, None, None)
