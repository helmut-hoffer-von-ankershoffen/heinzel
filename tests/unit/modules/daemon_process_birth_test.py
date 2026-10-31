"""Only a current manager-bound Linux process with unambiguous birth ordering may attest a handover."""

import os
import time

import pytest

from heinzel.modules.system.daemon import _process_birth as birth

pytestmark = pytest.mark.unit


def stat_line(pid, ticks, command=b"fixture (with spaces)"):
    fields = [b"S", *([b"0"] * 18), str(ticks).encode(), b"0"]
    return str(pid).encode() + b" (" + command + b") " + b" ".join(fields)


def test_linux_reads_only_current_process_birth_from_bounded_proc_stat(tmp_path, monkeypatch):
    path = tmp_path / "stat"
    path.write_bytes(stat_line(os.getpid(), 101))
    monkeypatch.setattr(birth, "_time_namespace", lambda: (7, 8))
    monkeypatch.setattr(birth, "_clock_ticks", lambda: 100)
    result = birth.current_birth("linux", stat_path=path)
    assert result == {"ticks": 101, "hz": 100, "ns_dev": 7, "ns_ino": 8}
    assert birth.after_request({"ticks": 100, "hz": 100, "ns_dev": 7, "ns_ino": 8}, result)
    assert not birth.after_request({"ticks": 101, "hz": 100, "ns_dev": 7, "ns_ino": 8}, result)
    assert not birth.after_request({"ticks": 100, "hz": 100, "ns_dev": 7, "ns_ino": 9}, result)
    assert not birth.after_request({"ticks": 100, "hz": 99, "ns_dev": 7, "ns_ino": 8}, result)


@pytest.mark.parametrize("kind", ["wrong-pid", "missing", "bad-start", "too-long", "namespace-changed"])
def test_linux_birth_fails_closed_for_ambiguous_evidence(tmp_path, monkeypatch, kind):
    path = tmp_path / "stat"
    content = stat_line(os.getpid(), 101)
    if kind == "wrong-pid":
        content = stat_line(os.getpid() + 1, 101)
    elif kind == "missing":
        content = b"malformed"
    elif kind == "bad-start":
        content = stat_line(os.getpid(), 0)
    elif kind == "too-long":
        content += b"x" * 9000
    path.write_bytes(content)
    namespaces = iter([(7, 8), (7, 9)] if kind == "namespace-changed" else [(7, 8), (7, 8)])
    monkeypatch.setattr(birth, "_time_namespace", lambda: next(namespaces))
    monkeypatch.setattr(birth, "_clock_ticks", lambda: 100)
    assert birth.current_birth("linux", stat_path=path) is None


def test_request_fence_uses_boottime_and_matching_time_namespace(monkeypatch):
    monkeypatch.setattr(birth, "_time_namespace", lambda: (7, 8))
    monkeypatch.setattr(birth, "_clock_ticks", lambda: 100)
    monkeypatch.setattr(time, "CLOCK_BOOTTIME", 7, raising=False)
    monkeypatch.setattr(time, "clock_gettime_ns", lambda clock: 1_009_999_999 if clock == 7 else 0)
    assert birth.request_fence("linux") == {"ticks": 100, "hz": 100, "ns_dev": 7, "ns_ino": 8}
    monkeypatch.setattr(birth, "_mac_witness", lambda: None)
    monkeypatch.setattr(birth, "_mac_unique", lambda _pid: None)
    assert birth.request_fence("darwin") is None
    assert birth.current_birth("darwin") is None
    namespaces = iter([(7, 8), (7, 9)])
    monkeypatch.setattr(birth, "_time_namespace", lambda: next(namespaces))
    assert birth.request_fence("linux") is None


def test_macos_witness_uses_held_child_and_orders_actual_self_identifier(monkeypatch):
    commands = []

    class Pipe:
        def close(self):
            commands.append("close")

    class Child:
        pid = 4545
        stdin = Pipe()
        returncode = None

        def poll(self):
            return None

        def wait(self, timeout):
            assert timeout == 2
            self.returncode = 0
            commands.append("reap")
            return 0

    def launch(command, **kwargs):
        assert command == ["/bin/cat"]
        assert kwargs["stdin"] == birth.subprocess.PIPE
        commands.append("launch")
        return Child()

    monkeypatch.setattr(birth.subprocess, "Popen", launch)
    monkeypatch.setattr(birth, "_mac_unique", lambda pid: (100, 1) if pid == os.getpid() else (101, 100))
    fence = birth.request_fence("darwin")
    assert fence == {"ticks": 101, "hz": 0, "ns_dev": 0, "ns_ino": 0}
    assert commands == ["launch", "close", "reap"]
    monkeypatch.setattr(birth, "_mac_unique", lambda _pid: (102, 10))
    actual = birth.current_birth("darwin")
    assert actual is not None
    assert birth.after_request(fence, actual)
    assert not birth.after_request(fence, {**actual, "ticks": 101})


def test_macos_refuses_foreign_parent_and_still_reaps_owned_witness(monkeypatch):
    events = []

    class Pipe:
        def close(self):
            events.append("close")

    class Child:
        pid = 4545
        stdin = Pipe()
        returncode = None

        def poll(self):
            return None

        def wait(self, timeout):
            assert timeout == 2
            self.returncode = 0
            events.append("reap")
            return 0

    monkeypatch.setattr(birth.subprocess, "Popen", lambda *_args, **_kwargs: Child())
    monkeypatch.setattr(birth, "_mac_unique", lambda pid: (100, 1) if pid == os.getpid() else (101, 999))
    assert birth.request_fence("darwin") is None
    assert events == ["close", "reap"]


def test_macos_witness_unavailable_when_child_cannot_launch(monkeypatch):
    def denied(*_args, **_kwargs):
        msg = "denied"
        raise OSError(msg)

    monkeypatch.setattr(birth, "_mac_unique", lambda _pid: (100, 1))
    monkeypatch.setattr(birth.subprocess, "Popen", denied)
    assert birth.request_fence("darwin") is None
