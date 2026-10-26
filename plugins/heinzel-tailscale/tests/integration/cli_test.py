"""The CLI boundary bounds and reaps real fake-client subprocesses without invoking installed Tailscale."""

import subprocess
import sys

import pytest
from heinzel_tailscale import _cli
from heinzel_tailscale._cli import TailscaleCli

from heinzel import HeinzelError

pytestmark = pytest.mark.integration


class PythonCli(TailscaleCli):
    def executable(self):
        return sys.executable


def test_bounded_client_keeps_stdout_stderr_separate_and_passes_literal_arguments():
    result = PythonCli().run(
        (
            "-I",
            "-S",
            "-c",
            "import sys; print(sys.argv[1]); print('diagnostic', file=sys.stderr)",
            "literal; $(never-a-shell)",
        )
    )
    assert result.returncode == 0
    assert result.stdout == b"literal; $(never-a-shell)\n"
    assert result.stderr == b"diagnostic\n"


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_output_overflow_stops_and_reaps_the_client(monkeypatch, stream):
    monkeypatch.setattr(_cli, "MAX_BYTES", 1024)
    processes = []
    original = subprocess.Popen

    def start(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(_cli.subprocess, "Popen", start)
    with pytest.raises(HeinzelError, match="size limit"):
        PythonCli().run(("-I", "-S", "-c", f"import sys; sys.{stream}.write('x' * 100000)"))
    assert len(processes) == 1
    assert processes[0].poll() is not None


def test_expired_deadline_reaps_a_client_that_never_answers(monkeypatch):
    monkeypatch.setattr(_cli, "TIMEOUT", 0)
    processes = []
    original = subprocess.Popen

    def start(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(_cli.subprocess, "Popen", start)
    with pytest.raises(HeinzelError, match="five seconds"):
        PythonCli().run(("-I", "-S", "-c", "import threading; threading.Event().wait()"))
    assert processes[0].poll() is not None


def test_interrupted_read_still_reaps_the_owned_client(monkeypatch):
    processes = []
    original = subprocess.Popen

    def start(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    def interrupted(_process):
        raise KeyboardInterrupt

    monkeypatch.setattr(_cli.subprocess, "Popen", start)
    monkeypatch.setattr(_cli, "_read", interrupted)
    with pytest.raises(KeyboardInterrupt):
        PythonCli().run(("-I", "-S", "-c", "import threading; threading.Event().wait()"))
    assert processes[0].poll() is not None


def test_client_exit_does_not_leave_a_descendant_that_ignores_term():
    import contextlib
    import os
    import signal

    child = (
        "import signal,threading; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "print('ready',flush=True); threading.Event().wait()"
    )
    parent = (
        "import subprocess,sys; "
        f"child=subprocess.Popen([sys.executable,'-I','-S','-c',{child!r}],"
        "stdout=subprocess.PIPE,stderr=subprocess.DEVNULL); "
        "child.stdout.readline(); print(child.pid,flush=True)"
    )
    result = PythonCli().run(("-I", "-S", "-c", parent))
    pid = int(result.stdout.strip())
    try:
        state = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, check=False
        ).stdout.strip()
        assert not state or state.startswith(b"Z"), f"owned descendant remains alive: {state!r}"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
