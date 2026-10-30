"""Real process groups are stopped when the step runner receives termination signals."""

import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_RUNNER = Path(__file__).resolve().parents[2] / "scripts" / "steps.py"


def _alive(pid: int) -> bool:
    result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)
    status = result.stdout.strip()
    return bool(status) and not status.startswith("Z")


def _wait_for(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    pytest.fail("Timed out waiting for isolated helper processes")


@pytest.mark.parametrize("sent", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize("verbose", [False, True])
def test_interrupt_stops_only_owned_children_and_grandchildren(tmp_path, sent, verbose):
    helper = tmp_path / "helper.py"
    grand = tmp_path / "grand.py"
    grand.write_text(
        "import os,signal,sys,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "Path(sys.argv[1], 'grandchild').write_text(str(os.getpid())); time.sleep(60)"
    )
    helper.write_text(
        "import os,signal,subprocess,sys,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "subprocess.Popen([sys.executable,sys.argv[2],sys.argv[1]]); "
        "Path(sys.argv[1], 'child').write_text(str(os.getpid())); time.sleep(60)"
    )
    directories = [tmp_path / f"step-{i}" for i in range(1 if verbose else 2)]
    for directory in directories:
        directory.mkdir()
    commands = [
        " ".join(map(shlex.quote, (sys.executable, str(helper), str(directory), str(grand))))
        for directory in directories
    ]
    environment = dict(os.environ, VERBOSE="1" if verbose else "0")
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    runner = subprocess.Popen(
        [
            sys.executable,
            str(_RUNNER),
            *[f"{'&' if i else ''}helper{i}={command}" for i, command in enumerate(commands)],
        ],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pids = []
    try:
        _wait_for(
            lambda: all((directory / name).is_file() for directory in directories for name in ("child", "grandchild"))
        )
        pids = [int((directory / name).read_text()) for directory in directories for name in ("child", "grandchild")]
        assert all(_alive(pid) for pid in pids)
        os.kill(runner.pid, sent)
        if sent == signal.SIGTERM:
            time.sleep(0.05)
            os.kill(runner.pid, signal.SIGINT)
        runner.communicate(timeout=8)
        assert runner.returncode == 128 + sent
        _wait_for(lambda: all(not _alive(pid) for pid in pids))
        assert unrelated.poll() is None
    finally:
        if runner.poll() is None:
            os.killpg(runner.pid, signal.SIGKILL)
            runner.wait(timeout=2)
        for directory in directories:
            for name in ("child", "grandchild"):
                path = directory / name
                if path.is_file():
                    pid = int(path.read_text())
                    if _alive(pid):
                        os.kill(pid, signal.SIGKILL)
        if unrelated.poll() is None:
            os.killpg(unrelated.pid, signal.SIGKILL)
            unrelated.wait(timeout=2)


def test_failed_step_cancels_concurrent_group_and_never_starts_later_step(tmp_path):
    failure = " ".join(map(shlex.quote, (sys.executable, "-c", "import sys; sys.exit(7)")))
    slow = " ".join(map(shlex.quote, (sys.executable, "-c", "import time; time.sleep(60)")))
    marker = tmp_path / "should-not-run"
    later = " ".join(
        map(shlex.quote, (sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"))
    )
    result = subprocess.run(
        [sys.executable, str(_RUNNER), f"fail={failure}", f"&slow={slow}", f"later={later}"],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    assert result.returncode == 7
    assert "fail" in result.stdout
    assert "cancelled" in result.stdout
    assert not marker.exists()


@pytest.mark.parametrize("verbose", ["0", "1"])
def test_timeout_has_standard_exit_code_in_both_modes(verbose):
    slow = " ".join(map(shlex.quote, (sys.executable, "-c", "import time; time.sleep(60)")))
    result = subprocess.run(
        [sys.executable, str(_RUNNER), f"slow={slow}"],
        env=dict(os.environ, VERBOSE=verbose, STEP_TIMEOUT="0.1"),
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    assert result.returncode == 124
