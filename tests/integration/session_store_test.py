"""A killed writer leaves a recoverable checkpoint or an explicitly damaged tail, never a silent replay."""

import signal
import subprocess
import sys
from datetime import UTC, datetime
from time import perf_counter

import pytest

from heinzel import HeinzelError, SessionStore

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 25, 10, tzinfo=UTC)


def child(root, program):
    return subprocess.run([sys.executable, "-c", program, str(root)], check=False, capture_output=True, timeout=10)


def test_process_death_after_fsync_before_index_commit_does_not_duplicate_a_checkpoint(tmp_path):
    root = tmp_path / "sessions"
    program = """
import os, signal, sys
from pathlib import Path
from heinzel import SessionStore
from heinzel.kernel import _session_store
store = SessionStore(Path(sys.argv[1]))
def killed(*args):
    os.kill(os.getpid(), signal.SIGKILL)
_session_store.SessionStore._insert = staticmethod(killed)
with store.writer('one') as writer:
    writer.append('turn.started', key='request-1', turn='turn-1')
"""
    result = child(root, program)
    assert result.returncode == -signal.SIGKILL
    sessions = SessionStore(root)
    with sessions.writer("one") as writer:
        recovered = writer.append("turn.started", key="request-1", turn="turn-1")
    assert recovered.seq == 1
    assert len(sessions.read("one").events) == 1
    assert sessions.heads()[0].problem is None


def test_a_process_killed_mid_append_keeps_a_readable_prefix_and_isolates_the_damage(tmp_path):
    root = tmp_path / "sessions"
    program = """
import os, signal, sys
from pathlib import Path
from heinzel import SessionStore
store = SessionStore(Path(sys.argv[1]))
with store.writer('one') as writer:
    writer.append('turn.started', key='request-1')
    descriptor = os.open(store.root / 'one/session.jsonl', os.O_APPEND | os.O_WRONLY)
    os.write(descriptor, b'{"v":1')
    os.kill(os.getpid(), signal.SIGKILL)
"""
    result = child(root, program)
    assert result.returncode == -signal.SIGKILL
    sessions = SessionStore(root)
    read = sessions.read("one")
    assert len(read.events) == 1
    assert read.problem
    with sessions.writer("one") as writer, pytest.raises(HeinzelError):
        writer.append("turn.finished")
    with sessions.writer("two") as writer:
        assert writer.append("turn.started").seq == 1


def test_another_process_cannot_write_while_the_session_lock_is_held(tmp_path):
    root = tmp_path / "sessions"
    program = """
import sys
from pathlib import Path
from heinzel import HeinzelError, SessionStore
try:
    with SessionStore(Path(sys.argv[1])).writer('one'):
        raise AssertionError('a second writer acquired the lock')
except HeinzelError as error:
    assert 'another writer' in str(error)
"""
    sessions = SessionStore(root)
    with sessions.writer("one"):
        result = child(root, program)
    assert result.returncode == 0, result.stderr.decode()
    # Kernel releases the lock on process exit, even when the owner was killed.
    with sessions.writer("one") as writer:
        assert writer.append("created").seq == 1


@pytest.mark.perf
@pytest.mark.timeout(30)
def test_five_hundred_indexed_sessions_list_in_under_fifty_milliseconds(tmp_path):
    sessions = SessionStore(tmp_path / "sessions", clock=lambda: NOW)
    for number in range(500):
        with sessions.writer(f"s-{number}") as writer:
            writer.append("state.changed", data={"state": "asleep"})
    start = perf_counter()
    heads = sessions.heads()
    elapsed = perf_counter() - start
    assert len(heads) == 500
    assert elapsed < 0.05
