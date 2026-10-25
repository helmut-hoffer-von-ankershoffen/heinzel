"""The storage contract is independent of a module's event vocabulary or model provider."""

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from heinzel import HeinzelError, SessionEvent, SessionStore
from heinzel.kernel import _session_store

pytestmark = pytest.mark.unit
NOW = datetime(2026, 10, 25, 10, tzinfo=UTC)


def store(tmp_path, **kwargs):
    return SessionStore(tmp_path / "sessions", clock=lambda: NOW, **kwargs)


def append(sessions, session="one", kind="turn.started", **kwargs):
    with sessions.writer(session) as writer:
        return writer.append(kind, **kwargs)


def test_the_envelope_chains_exact_bytes_and_fsyncs_before_indexing(tmp_path, monkeypatch):
    sessions = store(tmp_path)
    calls = []
    sync = os.fsync
    insert = SessionStore._insert

    def fsync(descriptor):
        calls.append("sync")
        sync(descriptor)

    def inserted(index, session, event):
        assert calls[-1] == "sync"
        calls.append("index")
        insert(index, session, event)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(SessionStore, "_insert", staticmethod(inserted))
    first = append(sessions, key="wake-1", turn="turn-1", data={"size": 3})
    second = append(sessions, kind="turn.finished", turn="turn-1")
    lines = (sessions.root / "one/session.jsonl").read_bytes().splitlines(keepends=True)
    assert first.seq == 1
    assert first.prev is None
    assert first.ts == NOW
    assert second.seq == 2
    assert second.prev == hashlib.sha256(lines[0]).hexdigest()
    assert sessions.read("one").events == [first, second]
    assert calls.count("index") == 2


def test_a_checkpoint_key_is_idempotent_and_conflicting_data_is_refused(tmp_path):
    sessions = store(tmp_path)
    first = append(sessions, key="request-1", data={"count": 1})
    assert append(sessions, key="request-1", data={"count": 1}) == first
    with pytest.raises(HeinzelError, match="different data"):
        append(sessions, key="request-1", data={"count": 2})
    # One effect has several checkpoints with the same key, distinguished by kind.
    append(sessions, kind="effect.done", key="request-1")
    assert len(sessions.read("one").events) == 2


@pytest.mark.parametrize(
    ("first", "second"), [({"enabled": True}, {"enabled": 1}), ({"nested": [False]}, {"nested": [0]})]
)
def test_idempotency_distinguishes_json_booleans_from_numbers(tmp_path, first, second):
    sessions = store(tmp_path)
    append(sessions, key="request-1", data=first)
    with pytest.raises(HeinzelError, match="different data"):
        append(sessions, key="request-1", data=second)


def test_non_json_data_is_refused_even_when_a_key_already_exists(tmp_path):
    sessions = store(tmp_path)
    append(sessions, key="request-1", data={"values": [1]})
    with pytest.raises(ValidationError):
        append(sessions, key="request-1", data={"values": (1,)})


def test_index_failure_after_durable_append_recovers_without_a_second_event(tmp_path, monkeypatch):
    sessions = store(tmp_path)
    insert = SessionStore._insert

    def fail(*_args):
        msg = "simulated index failure"
        raise sqlite3.OperationalError(msg)

    monkeypatch.setattr(SessionStore, "_insert", staticmethod(fail))
    with pytest.raises(sqlite3.OperationalError, match="simulated"):
        append(sessions, key="request-1")
    monkeypatch.setattr(SessionStore, "_insert", staticmethod(insert))
    event = append(sessions, key="request-1")
    assert event.seq == 1
    assert len(sessions.read("one").events) == 1


def test_unknown_kinds_are_verified_then_skipped_by_the_reader(tmp_path):
    sessions = store(tmp_path)
    first = append(sessions)
    append(sessions, kind="future.added", data={"new_field": True})
    final = append(sessions, kind="turn.finished")
    assert sessions.read("one", kinds={"turn.started", "turn.finished"}).events == [first, final]
    assert sessions.read("one").problem is None


@pytest.mark.parametrize("damage", ["partial", "edited", "deleted-last", "sequence", "version"])
def test_damage_is_contained_and_never_repaired_by_a_read(tmp_path, damage):
    sessions = store(tmp_path)
    append(sessions)
    append(sessions, kind="turn.finished", data={"size": 1})
    path = sessions.root / "one/session.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    if damage == "partial":
        path.write_bytes(b"".join(lines) + b'{"v":1')
    elif damage == "deleted-last":
        path.write_bytes(lines[0])
    else:
        data = json.loads(lines[-1])
        if damage == "edited":
            data["data"] = {"size": 2}
        elif damage == "sequence":
            data["seq"] = 9
        else:
            data["v"] = 2
        path.write_bytes(lines[0] + (json.dumps(data) + "\n").encode())
    damaged = path.read_bytes()
    assert sessions.read("one").problem
    assert sessions.reindex()[0].problem
    assert sessions.reindex()[0].problem  # rebuilding twice must not accept a changed tail
    assert path.read_bytes() == damaged
    with pytest.raises(HeinzelError):
        append(sessions, kind="turn.finished")
    assert append(sessions, session="two").seq == 1


def test_missing_index_is_rebuilt_and_queries_do_not_open_unchanged_spines(tmp_path, monkeypatch):
    sessions = store(tmp_path)
    append(sessions, data={"state": "waiting"})
    expected = sessions.heads()
    (sessions.root / "index.sqlite").unlink()
    assert sessions.heads() == expected

    def no_scan(_folder):
        pytest.fail("An indexed listing must not open an unchanged spine")

    monkeypatch.setattr(_session_store, "scan", no_scan)
    assert sessions.heads() == expected


def test_a_broken_index_is_quarantined_and_rebuilt(tmp_path):
    sessions = store(tmp_path)
    append(sessions, data={"state": "asleep"})
    expected = sessions.heads()
    (sessions.root / "index.sqlite").write_bytes(b"not a sqlite database")
    assert sessions.heads() == expected
    assert len(list(sessions.root.glob(".index-*.sqlite"))) == 1


def test_a_projection_is_rebuildable_and_skips_unknown_kinds(tmp_path):
    def project(previous, event):
        return {**previous, **event.data} if event.kind == "state.changed" else previous

    sessions = store(tmp_path, project=project, projection_version="state-v1")
    append(sessions, kind="state.changed", data={"state": "waiting", "version": 1})
    append(sessions, kind="future.added", data={"untrusted": "ignored"})
    expected = sessions.heads()
    assert expected[0].projection == {"state": "waiting", "version": 1}
    assert sessions.reindex() == expected
    assert store(tmp_path, project=project, projection_version="state-v2").heads() == expected


def test_reprojection_keeps_the_checkpoint_that_detects_a_changed_tail(tmp_path):
    sessions = store(tmp_path)
    append(sessions, data={"count": 1})
    path = sessions.root / "one/session.jsonl"
    data = json.loads(path.read_text())
    data["data"] = {"count": 2}
    path.write_text(json.dumps(data) + "\n")
    assert store(tmp_path, projection_version="v2").heads()[0].problem


def test_turn_bodies_are_private_opaque_json_and_do_not_enter_the_spine(tmp_path):
    sessions = store(tmp_path)
    conversation = {"messages": [{"role": "user", "content": "A private draft"}]}
    with sessions.writer("one") as writer:
        digest = writer.append_body("turn-1", conversation)
        writer.append_body("turn-1", {"tool": "read", "result": [1, 2]})
        assert writer.body("turn-1") == [conversation, {"tool": "read", "result": [1, 2]}]
        writer.append("step.finished", data={"body_hash": digest}, turn="turn-1")
    assert "A private draft" not in (sessions.root / "one/session.jsonl").read_text()
    for path in sessions.root.rglob("*"):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)


def test_expired_or_damaged_bodies_do_not_break_the_spine(tmp_path):
    sessions = store(tmp_path)
    append(sessions)
    with sessions.writer("one") as writer:
        writer.append_body("turn-1", {"message": "private"})
        path = sessions.root / "one/turns/turn-1.jsonl"
        for damaged in [b"{}", b"not-json\n"]:
            path.write_bytes(damaged)
            with pytest.raises(HeinzelError, match="turn body"):
                writer.body("turn-1")
            with pytest.raises(HeinzelError, match="turn body"):
                writer.append_body("turn-1", {"new": "checkpoint"})
            assert path.read_bytes() == damaged
        path.unlink()
        with pytest.raises(FileNotFoundError):
            writer.body("turn-1")
    assert sessions.read("one").problem is None


def test_a_saved_hash_detects_valid_json_edits_before_resume(tmp_path):
    sessions = store(tmp_path)
    with sessions.writer("one") as writer:
        digest = writer.append_body("turn-1", {"message": "original"})
        assert writer.body("turn-1", expected_hash=digest) == [{"message": "original"}]
        (sessions.root / "one/turns/turn-1.jsonl").write_text('{"message":"edited"}\n')
        with pytest.raises(HeinzelError, match="saved checkpoint"):
            writer.body("turn-1", expected_hash=digest)
        with pytest.raises(HeinzelError, match="hex digest"):
            writer.body("turn-1", expected_hash="not-a-hash")


def test_a_committed_body_can_be_read_while_the_session_writer_is_active(tmp_path):
    sessions = store(tmp_path)
    with sessions.writer("one") as writer:
        digest = writer.append_body("snapshot-1", {"message": "committed"})
        writer.append("step.finished", data={"body_id": "snapshot-1", "body_hash": digest})
        writer.append_body("snapshot-2", {"message": "new work"})
        assert sessions.body("one", "snapshot-1", expected_hash=digest) == [{"message": "committed"}]
    assert sessions.body("one", "snapshot-1", expected_hash=digest) == [{"message": "committed"}]


def test_public_body_read_requires_a_hash_and_rejects_changed_or_missing_content(tmp_path):
    sessions = store(tmp_path)
    with sessions.writer("one") as writer:
        digest = writer.append_body("snapshot-1", {"message": "original"})
    with pytest.raises(HeinzelError, match="hex digest"):
        sessions.body("one", "snapshot-1", expected_hash="not-a-hash")
    with pytest.raises(TypeError):
        sessions.body("one", "snapshot-1", expected_hash=None)
    path = sessions.root / "one/turns/snapshot-1.jsonl"
    path.write_text('{"message":"edited"}\n')
    with pytest.raises(HeinzelError, match="saved checkpoint"):
        sessions.body("one", "snapshot-1", expected_hash=digest)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        sessions.body("one", "snapshot-1", expected_hash=digest)


def test_public_body_read_cannot_follow_a_symlink_or_escape_its_session(tmp_path):
    sessions = store(tmp_path)
    with sessions.writer("one") as writer:
        digest = writer.append_body("snapshot-1", {"message": "private"})
    outside = tmp_path / "outside"
    outside.write_text('{"message":"outside"}\n')
    outside.chmod(0o600)
    path = sessions.root / "one/turns/snapshot-1.jsonl"
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(OSError, match="snapshot-1"):
        sessions.body("one", "snapshot-1", expected_hash=digest)
    with pytest.raises(HeinzelError):
        sessions.body("../outside", "snapshot-1", expected_hash=digest)
    with pytest.raises(HeinzelError):
        sessions.body("one", "../outside", expected_hash=digest)
    assert outside.read_text() == '{"message":"outside"}\n'


def test_a_root_symlink_reports_safe_guidance_without_the_private_path(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    path = tmp_path / "linked-root"
    path.symlink_to(private)
    with pytest.raises(HeinzelError, match="cannot be opened safely") as failure:
        SessionStore(path).heads()
    assert str(tmp_path) not in str(failure.value)
    assert failure.value.__suppress_context__


@pytest.mark.parametrize("value", ["../other", "/outside", "a/b", "", ".", "a\x00b"])
def test_paths_cannot_escape_the_store(tmp_path, value):
    sessions = store(tmp_path)
    with pytest.raises(HeinzelError), sessions.writer(value):
        pass
    with sessions.writer("one") as writer, pytest.raises(HeinzelError):
        writer.append_body(value, None)


@pytest.mark.parametrize("target", ["session.jsonl", "lock", "index.sqlite", "index.lock"])
def test_existing_symlinks_are_never_followed(tmp_path, target):
    sessions = store(tmp_path)
    append(sessions)
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    parent = sessions.root / "one" if target in {"session.jsonl", "lock"} else sessions.root
    path = parent / target
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises((HeinzelError, OSError)):
        append(sessions)
    assert outside.read_text() == "untouched"
    if target == "session.jsonl":
        assert append(sessions, session="two").seq == 1


def test_hardlinks_and_public_permissions_are_refused(tmp_path):
    sessions = store(tmp_path)
    append(sessions)
    outside = tmp_path / "outside"
    outside.write_text("private")
    outside.chmod(0o600)
    path = sessions.root / "one/session.jsonl"
    path.unlink()
    path.hardlink_to(outside)
    with pytest.raises(HeinzelError):
        append(sessions)
    path.unlink()
    sessions.root.chmod(0o755)
    with pytest.raises(HeinzelError, match="0700"):
        sessions.heads()


def test_oversized_checkpoints_and_naive_timestamps_are_refused(tmp_path):
    sessions = store(tmp_path)
    with pytest.raises(HeinzelError, match="too large"):
        append(sessions, data={"long": "x" * 65536})
    with pytest.raises(ValidationError, match="offset"):
        SessionEvent(seq=1, ts=NOW.replace(tzinfo=None), prev=None, kind="created")
    with pytest.raises(ValidationError, match="version"):
        SessionEvent.model_validate_json('{"v":true,"seq":1,"ts":"2026-10-25T00:00:00Z","prev":null,"kind":"created"}')


def test_a_writer_cannot_outlive_its_lock_or_open_a_second_writer(tmp_path):
    sessions = store(tmp_path)
    with (
        sessions.writer("one") as writer,
        pytest.raises(HeinzelError, match="another writer"),
        sessions.writer("one"),
    ):
        pass
    with pytest.raises(HeinzelError, match="writer context"):
        writer.append("created")


def test_a_missing_spine_is_reported_without_losing_its_checkpoint(tmp_path):
    sessions = store(tmp_path)
    event = append(sessions)
    (sessions.root / "one/session.jsonl").unlink()
    head = sessions.heads()[0]
    assert head.seq == event.seq
    assert head.problem == "Session spine is missing"
    with pytest.raises(HeinzelError, match="missing"):
        append(sessions)


def test_unsafe_directory_permissions_do_not_block_other_sessions_and_restore_cleanly(tmp_path):
    sessions = store(tmp_path)
    append(sessions)
    folder = sessions.root / "one"
    folder.chmod(0o755)
    assert append(sessions, session="two").seq == 1
    assert sessions.heads()[0].problem
    folder.chmod(0o700)
    assert sessions.read("one").problem is None
