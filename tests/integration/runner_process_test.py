"""`run_bounded` with real processes: its own session, a timeout that kills the whole group, capped output.

The processes are small Python scripts, never an agent.
"""

import os
import sys
import time
from pathlib import Path

import pytest

from heinzel.modules.system.runner._process import run_bounded

pytestmark = pytest.mark.integration

ENV = {"PATH": os.environ.get("PATH", "")}


def python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_passes_input_and_returns_output_and_exit_code(tmp_path):
    outcome = run_bounded(
        python("import sys; d = sys.stdin.read(); print(d.upper()); print('warn', file=sys.stderr); sys.exit(3)"),
        input="hello",
        cwd=tmp_path,
        env=ENV,
        timeout=10,
    )
    assert (outcome.returncode, outcome.stdout.strip(), outcome.stderr_tail(), outcome.truncated) == (
        3,
        "HELLO",
        "warn",
        False,
    )
    assert not outcome.timed_out


def test_runs_in_its_own_session(tmp_path):
    outcome = run_bounded(
        python("import os; print(os.getsid(0) == os.getpid())"), input=None, cwd=tmp_path, env=ENV, timeout=10
    )
    assert outcome.stdout.strip() == "True"


def test_a_timeout_kills_the_whole_process_group_even_a_child_ignoring_sigterm(tmp_path):
    pid_file = tmp_path / "child.pid"
    code = (
        "import signal, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    outcome = run_bounded(python(code), input=None, cwd=tmp_path, env=ENV, timeout=1)
    assert outcome.timed_out
    assert time.monotonic() - started < 10
    child = int(Path(pid_file).read_text())
    for _ in range(50):
        if not alive(child):
            break
        time.sleep(0.05)
    assert not alive(child), "a tool process outlived the run"


def test_output_beyond_the_limit_is_drained_and_dropped(tmp_path):
    outcome = run_bounded(
        python("import sys; sys.stdout.write('x' * 100000)"), input=None, cwd=tmp_path, env=ENV, timeout=10, limit=1000
    )
    assert outcome.returncode == 0
    assert len(outcome.stdout) == 1000
    assert outcome.truncated


def test_an_agent_that_ignores_its_input_does_not_hang_the_run(tmp_path):
    outcome = run_bounded(python("pass"), input="x" * 1_000_000, cwd=tmp_path, env=ENV, timeout=10)
    assert outcome.returncode == 0
