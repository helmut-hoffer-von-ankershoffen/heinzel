"""Draft-in-turn, tampered journals and provider drift: the captured write host refuses each exactly."""

import pytest

from heinzel import CallIdentity, HeinzelError, Principal
from heinzel.kernel._approval import ApprovalConfirmation
from heinzel.modules.heinzel._captured_write_host import CapturedWriteHost, review_payload
from heinzel.modules.heinzel._session_bodies import immutable_body
from tests.unit.modules.captured_write_host_test import _confirm, _setup

pytestmark = pytest.mark.unit


def _append(crud, session, kind, *, key, data=None, turn=None):  # noqa: PLR0913
    with crud.repository.control_writer(session) as writer:
        if turn is None:
            writer.append(kind, key=key, data=data or {})
        else:
            writer.append(kind, key=key, turn=turn, data=data or {})


def _events(crud, draft, kind):
    return [e for e in crud.repository.control_read(draft.session).events if e.kind == kind]


def test_a_model_draft_needs_its_turn_lock_and_an_active_accepted_turn(tmp_path):
    context, _current, crud, draft, _provider, host = _setup(tmp_path)
    try:
        with crud.repository.main_writer(draft.session) as writer:
            with pytest.raises(HeinzelError, match="active accepted turn"):
                host.draft_in_turn(draft, "turn-one", writer)
            _append(crud, draft.session, "interactive.started", key="turn-one", turn="turn-one")
            with (
                crud.repository.control_writer(draft.session) as not_the_turn_lock,
                pytest.raises(HeinzelError, match="owning turn lock"),
            ):
                host.draft_in_turn(draft, "turn-one", not_the_turn_lock)
            host.draft_in_turn(draft, "turn-one", writer)
            host.draft_in_turn(draft, "turn-one", writer)
            assert len(_events(crud, draft, "write.drafted")) == 1
            assert _events(crud, draft, "write.drafted")[0].turn == "turn-one"
            with pytest.raises(HeinzelError, match="already identifies different terms"):
                host.draft_in_turn(draft.model_copy(update={"section": "other"}), "turn-one", writer)
            _append(crud, draft.session, "interactive.started", key="turn-two", turn="turn-two")
            with pytest.raises(HeinzelError, match="already identifies different terms"):
                host.draft_in_turn(draft, "turn-two", writer)
            _append(crud, draft.session, "message.replied", key="turn-one", turn="turn-one")
            with pytest.raises(HeinzelError, match="active accepted turn"):
                host.draft_in_turn(draft, "turn-one", writer)
        assert [item["request_id"] for item in host.pending(draft.session)] == [draft.request_id]
    finally:
        context.__exit__(None, None, None)


def test_a_draft_for_another_owner_is_refused_and_the_daemon_needs_the_confirmed_review(tmp_path):
    context, current, crud, draft, _provider, host = _setup(tmp_path)
    try:
        foreign = draft.model_copy(update={"owner": "u-" + "c" * 32})
        with pytest.raises(HeinzelError, match="authenticated owner"):
            host.draft(foreign)
        assert _events(crud, draft, "write.drafted") == []
        host.draft(draft)
        confirmation = ApprovalConfirmation()
        trusted = CapturedWriteHost(crud, current, host.provider, channel_confirmation=confirmation)
        with current.local("daemon"):
            with pytest.raises(HeinzelError, match="authenticated owner"):
                trusted.current(draft.session, draft.request_id)
            with confirmation.using("heinzel", "execute-captured-write", review_payload(draft)):
                assert trusted.current(draft.session, draft.request_id) == draft
        stranger = CallIdentity(
            Principal("u-" + "b" * 32), "api-bearer", "other", "private", frozenset({"private:write"})
        )
        with (
            current.using(stranger),
            confirmation.using("heinzel", "execute-captured-write", review_payload(draft)),
            pytest.raises(HeinzelError, match="authenticated owner"),
        ):
            trusted.current(draft.session, draft.request_id)
    finally:
        context.__exit__(None, None, None)


def test_a_saved_draft_with_a_broken_body_or_another_identity_is_invalid(tmp_path):
    context, _current, crud, draft, _provider, host = _setup(tmp_path)
    try:
        with crud.repository.control_writer(draft.session) as writer:
            junk = immutable_body(writer, "captured-write-draft", {"unexpected": 1}, control=True)
            writer.append("write.drafted", key="junk", data=junk.model_dump(mode="json"))
            valid = immutable_body(writer, "captured-write-draft", review_payload(draft), control=True)
            writer.append("write.drafted", key="renamed", data=valid.model_dump(mode="json"))
        for request in ("junk", "renamed"):
            with pytest.raises(HeinzelError, match="draft is invalid"):
                host.current(draft.session, request)
    finally:
        context.__exit__(None, None, None)


def test_review_returns_the_exact_terms_and_every_provider_drift_blocks_review_and_issue(tmp_path, monkeypatch):
    context, _current, _crud, draft, provider, host = _setup(tmp_path)
    try:
        host.draft(draft)
        assert host.review(draft.session, draft.request_id) == review_payload(draft)
        provider.revision = "moved-on"
        for call in (host.review, lambda s, r: host.issue(s, r, "human-test")):
            with pytest.raises(HeinzelError, match="nothing was written"):
                call(draft.session, draft.request_id)
        provider.revision = "one"

        def refuse(_terms):
            msg = "PRIVATE credential detail"
            raise OSError(msg)

        monkeypatch.setattr(provider, "open_lease", refuse)
        with pytest.raises(HeinzelError, match="nothing was written") as error:
            host.review(draft.session, draft.request_id)
        assert "PRIVATE" not in str(error.value)
        assert provider.writes == 0
    finally:
        context.__exit__(None, None, None)


def test_a_draft_that_is_attempted_while_issuing_is_refused_before_a_proof_exists(tmp_path, monkeypatch):
    context, _current, crud, draft, _provider, host = _setup(tmp_path)
    try:
        host.draft(draft)

        def attempted_meanwhile(_draft):
            _append(crud, draft.session, "write.attempted", key=f"write-attempt-{draft.request_id}")

        monkeypatch.setattr(host, "_observe", attempted_meanwhile)
        with pytest.raises(HeinzelError, match="changed or was already attempted"):
            host.issue(draft.session, draft.request_id, "human-test")
        assert _events(crud, draft, "approval.issued") == []
    finally:
        context.__exit__(None, None, None)


def test_status_refuses_journals_that_were_tampered_with(tmp_path):
    invalid = "evidence is invalid"
    context, _current, crud, draft, _provider, host = _setup(tmp_path)
    try:
        host.draft(draft)
        _append(crud, draft.session, "write.acknowledged", key=draft.request_id)
        with pytest.raises(HeinzelError, match=invalid):
            host.status(draft.session, draft.request_id)
    finally:
        context.__exit__(None, None, None)

    context, _current, crud, draft, _provider, host = _setup(tmp_path / "discard")
    try:
        host.draft(draft)
        host.discard(draft.session, draft.request_id)
        _append(crud, draft.session, "write.attempted", key=f"write-attempt-{draft.request_id}")
        with pytest.raises(HeinzelError, match=invalid):
            host.status(draft.session, draft.request_id)
    finally:
        context.__exit__(None, None, None)

    context, _current, crud, draft, _provider, host = _setup(tmp_path / "unissued")
    try:
        host.draft(draft)
        _append(crud, draft.session, "write.attempted", key=f"write-attempt-{draft.request_id}")
        with pytest.raises(HeinzelError, match=invalid):
            host.status(draft.session, draft.request_id)
    finally:
        context.__exit__(None, None, None)

    context, _current, crud, draft, _provider, host = _setup(tmp_path / "unreadable")
    try:
        host.draft(draft)
        _append(crud, draft.session, "approval.issued", key="proof-x", data={"unexpected": 1})
        _append(
            crud,
            draft.session,
            "write.attempted",
            key=f"write-attempt-{draft.request_id}",
            data={"proof_hash": "proof-x"},
        )
        with pytest.raises(HeinzelError, match=invalid):
            host.status(draft.session, draft.request_id)
    finally:
        context.__exit__(None, None, None)


def _uncertain(tmp_path):
    context, current, crud, draft, provider, host = _setup(tmp_path)
    host.draft(draft)
    proof = host.issue(draft.session, draft.request_id, "human-test")
    provider.lose_ack = True
    with pytest.raises(HeinzelError, match="uncertain"):
        _confirm(host, draft, proof)
    assert host.status(draft.session, draft.request_id) == "uncertain"
    return context, current, crud, draft, provider, host


def test_a_replayed_nonce_or_a_malformed_receipt_makes_the_evidence_invalid(tmp_path):
    context, _current, crud, draft, _provider, host = _uncertain(tmp_path)
    try:
        attempt = _events(crud, draft, "write.attempted")[0]
        _append(
            crud,
            draft.session,
            "write.attempted",
            key="write-attempt-another",
            data={"consumed_nonce": attempt.data["consumed_nonce"]},
        )
        with pytest.raises(HeinzelError, match="evidence is invalid"):
            host.status(draft.session, draft.request_id)
    finally:
        context.__exit__(None, None, None)

    context, _current, crud, draft, _provider, host = _uncertain(tmp_path / "receipt")
    try:
        _append(crud, draft.session, "write.acknowledged", key=draft.request_id, data={"attempt_seq": 0})
        with pytest.raises(HeinzelError, match="evidence is invalid"):
            host.status(draft.session, draft.request_id)
        with pytest.raises(HeinzelError, match="evidence is invalid"):
            host.projection(draft.session, draft.request_id)
    finally:
        context.__exit__(None, None, None)

    context, _current, crud, draft, _provider, host = _uncertain(tmp_path / "twice")
    try:
        for _ in range(2):
            _append(crud, draft.session, "write.acknowledged", key=draft.request_id)
        with pytest.raises(HeinzelError, match="evidence is invalid"):
            host.status(draft.session, draft.request_id)
    finally:
        context.__exit__(None, None, None)


def test_projection_reports_the_status_until_an_acknowledged_receipt_exists(tmp_path):
    context, _current, _crud, draft, _provider, host = _setup(tmp_path)
    try:
        host.draft(draft)
        assert host.projection(draft.session, draft.request_id).status == "draft"
        proof = host.issue(draft.session, draft.request_id, "human-test")
        assert _confirm(host, draft, proof) == "acknowledged"
        projected = host.projection(draft.session, draft.request_id)
        assert projected.status == "acknowledged"
        assert projected.receipt is not None
        assert (projected.receipt.request_id, projected.receipt.revision) == (draft.request_id, "two")
        assert projected.receipt.base_revision == "one"
    finally:
        context.__exit__(None, None, None)


def test_a_provider_that_acknowledges_another_resource_leaves_the_write_uncertain(tmp_path, monkeypatch):
    context, _current, crud, draft, provider, host = _setup(tmp_path)
    try:
        host.draft(draft)
        proof = host.issue(draft.session, draft.request_id, "human-test")
        original = provider.open_lease

        def opener(terms):
            lease = original(terms)
            write = lease.write

            def other_resource():
                return write().model_copy(update={"reference": "fixture://document/other"})

            lease.write = other_resource
            return lease

        monkeypatch.setattr(provider, "open_lease", opener)
        with pytest.raises(HeinzelError, match="uncertain"):
            _confirm(host, draft, proof)
        assert host.status(draft.session, draft.request_id) == "uncertain"
        assert _events(crud, draft, "write.acknowledged") == []
    finally:
        context.__exit__(None, None, None)
