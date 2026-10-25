"""Personal identity and inbox acceptance survive restart without granting a model or another owner authority."""

import hashlib
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from heinzel import CallIdentity, CurrentUser, HeinzelError, Principal
from heinzel.modules.heinzel import _session_crud
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import HeinzelAppearance, MessageRequest, SessionCosts, SessionMessage
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit

AT = datetime(2026, 10, 25, tzinfo=UTC)


@pytest.fixture
def services(tmp_path):
    current = CurrentUser(tmp_path / "state" / "identity")
    repository = PersonalSessionRepository(current, clock=lambda: AT)
    service = SessionCrud(repository, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
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
