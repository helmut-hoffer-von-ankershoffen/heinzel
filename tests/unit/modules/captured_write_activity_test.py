"""Owner-private activity never turns uncertain writes into edit evidence."""

import pytest

from heinzel import CallIdentity, HeinzelError, Principal
from heinzel.modules.heinzel._captured_write_activity import captured_write_activity
from heinzel.modules.heinzel._captured_write_host import CapturedWriteHost
from tests.unit.modules.captured_write_host_test import _confirm, _setup

pytestmark = pytest.mark.unit


def test_only_recorded_acknowledgement_is_shown_as_such(tmp_path):
    context, current, crud, draft, provider, host = _setup(tmp_path)
    try:
        host.draft(draft)
        before = captured_write_activity(crud, current, draft.session)
        assert [item.status for item in before.items] == ["draft"]
        assert "private replacement" not in before.model_dump_json()
        assert provider.writes == 0
        proof = host.issue(draft.session, draft.request_id, "human-test")
        assert _confirm(host, draft, proof) == "acknowledged"
        page = captured_write_activity(crud, current, draft.session)
        assert [(item.status, item.revision) for item in page.items] == [("acknowledged", "two")]
        assert page.items[0].reference == draft.reference
        assert "private replacement" not in page.model_dump_json()
        assert provider.writes == 1
        foreign = CallIdentity(Principal("u-" + "f" * 32), "cli", "local", "fixture", frozenset({"private:write"}))
        with current.using(foreign), pytest.raises(HeinzelError):
            captured_write_activity(crud, current, draft.session)
        other = crud.new_chat("other").session_id
        assert captured_write_activity(crud, current, other).items == []
        assert provider.writes == 1
    finally:
        context.__exit__(None, None, None)


def test_lost_ack_is_uncertain_and_no_provider_call_repeated(tmp_path):
    context, current, crud, draft, provider, host = _setup(tmp_path)
    try:
        host.draft(draft)
        proof = host.issue(draft.session, draft.request_id, "human-test")
        provider.lose_ack = True
        with pytest.raises(HeinzelError):
            _confirm(host, draft, proof)
        page = captured_write_activity(crud, current, draft.session)
        assert [(item.status, item.revision) for item in page.items] == [("uncertain", None)]
        assert provider.writes == 1
    finally:
        context.__exit__(None, None, None)


def test_pagination_and_concurrent_journal_change_fail_closed(tmp_path, monkeypatch):
    context, current, crud, draft, provider, host = _setup(tmp_path)
    try:
        for number in range(23):
            host.draft(draft.model_copy(update={"request_id": f"draft-{number}"}))
        first = captured_write_activity(crud, current, draft.session)
        assert len(first.items) == 20
        assert first.next_before is not None
        second = captured_write_activity(crud, current, draft.session, before=first.next_before)
        assert len(second.items) == 3
        assert second.next_before is None
        assert not {item.request_id for item in first.items} & {item.request_id for item in second.items}
        original = CapturedWriteHost.projection
        changed = False

        def append_during_projection(self, session, request):
            nonlocal changed
            result = original(self, session, request)
            if not changed:
                changed = True
                with crud.repository.control_writer(session) as writer:
                    writer.append("fixture.changed", data={})
            return result

        monkeypatch.setattr(CapturedWriteHost, "projection", append_during_projection)
        with pytest.raises(HeinzelError, match="changed"):
            captured_write_activity(crud, current, draft.session)
        assert provider.writes == 0
    finally:
        context.__exit__(None, None, None)


def test_orphan_or_fabricated_acknowledgement_is_never_projected(tmp_path):
    context, current, crud, draft, provider, host = _setup(tmp_path)
    try:
        host.draft(draft)
        with crud.repository.control_writer(draft.session) as writer:
            writer.append("write.acknowledged", key=draft.request_id, data={"revision": "invented"})
        with pytest.raises(HeinzelError):
            captured_write_activity(crud, current, draft.session)
        assert provider.writes == 0
    finally:
        context.__exit__(None, None, None)


def test_acknowledgement_refuses_approval_before_draft(tmp_path, monkeypatch):
    context, _current, crud, draft, provider, host = _setup(tmp_path)
    try:
        host.draft(draft)
        proof = host.issue(draft.session, draft.request_id, "human-test")
        assert _confirm(host, draft, proof) == "acknowledged"
        saved = crud.repository.control_read(draft.session)
        issued = next(event for event in saved.events if event.kind == "approval.issued")
        reordered = [
            event.model_copy(update={"seq": issued.seq + 1}) if event.kind == "write.drafted" else event
            for event in saved.events
        ]
        # Simulate a verified journal view with a draft after issuance; no provider action occurs.
        monkeypatch.setattr(
            crud.repository, "control_read", lambda _session: saved.model_copy(update={"events": reordered})
        )
        with pytest.raises(HeinzelError, match="evidence is invalid"):
            host.projection(draft.session, draft.request_id)
        assert provider.writes == 1
    finally:
        context.__exit__(None, None, None)


def test_acknowledgement_without_a_key_fails_closed(tmp_path):
    context, current, crud, draft, provider, host = _setup(tmp_path)
    try:
        host.draft(draft)
        with crud.repository.control_writer(draft.session) as writer:
            writer.append("write.acknowledged", data={"revision": "invented"})
        with pytest.raises(HeinzelError, match="activity is inconsistent"):
            captured_write_activity(crud, current, draft.session)
        assert provider.writes == 0
    finally:
        context.__exit__(None, None, None)
