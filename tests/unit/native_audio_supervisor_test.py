"""Offline failure evidence: only disposable Python processes, never Electron."""

import contextlib
import importlib.util
import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import psutil
import pytest

pytestmark = pytest.mark.unit
SPEC = importlib.util.spec_from_file_location(
    "native_audio_fixture", Path(__file__).resolve().parents[1] / "e2e" / "native_system_audio_test.py"
)
assert SPEC
assert SPEC.loader
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)


def test_desktop_install_materializes_electron_without_launch(monkeypatch):
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(fixture, "_run_owned_group", run)
    monkeypatch.setenv("ELECTRON_SKIP_BINARY_DOWNLOAD", "1")
    fixture._prepare_desktop()
    commands = [call.args[0] for call in run.call_args_list]
    assert commands[0][-2:] == ["install", "--frozen-lockfile"]
    assert commands[1][-3:] == ["exec", "node", "node_modules/electron/install.js"]
    assert commands[2][-4:] == ["exec", "node", "-p", 'require("electron")']
    assert all("ELECTRON_SKIP_BINARY_DOWNLOAD" not in call.args[1] for call in run.call_args_list)
    assert all(call.kwargs["timeout"] == 180 for call in run.call_args_list)


def test_launch_failure_never_signals_an_unowned_group(monkeypatch):
    monkeypatch.setattr(fixture, "_require_process_containment", lambda: None)
    monkeypatch.setattr(fixture.subprocess, "Popen", Mock(side_effect=OSError("launch failed")))
    kill = Mock()
    monkeypatch.setattr(fixture.os, "killpg", kill)
    with pytest.raises(OSError, match="launch failed"):
        fixture._run_owned_group(["fixture"], {})
    kill.assert_not_called()


@pytest.mark.parametrize("detach_session", [False, True])
@pytest.mark.parametrize("controller_exits", [False, True])
@pytest.mark.parametrize("deny_group", [False, True])
def test_failure_terminates_real_tree_and_reaps_controller(
    tmp_path, monkeypatch, controller_exits, deny_group, detach_session
):
    # Exercise best-effort cleanup only; this is not containment evidence.
    monkeypatch.setattr(fixture, "_require_process_containment", lambda: None)
    record = tmp_path / "descendant"
    # The grandchild leaves the controller group (or session) and ignores TERM.
    descendant = (
        "import os,signal,time; "
        + ("os.setsid(); " if detach_session else "os.setpgid(0,0); ")
        + "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        f"open({str(record)!r},'w').write(str(os.getpid())); time.sleep(60)"
    )
    controller = (
        "import signal,subprocess,sys,time; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        f"subprocess.Popen([sys.executable,'-c',{descendant!r}], "
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        + ("time.sleep(.3); sys.exit(7)" if controller_exits else "time.sleep(60)")
    )
    spawned = []
    original = fixture.subprocess.Popen

    def popen(*args, **kwargs):
        child = original(*args, **kwargs)
        spawned.append(child)
        return child

    monkeypatch.setattr(fixture.subprocess, "Popen", popen)
    if deny_group:
        monkeypatch.setattr(fixture.os, "killpg", Mock(side_effect=PermissionError("macOS EPERM")))
    try:
        if controller_exits:
            result = fixture._run_owned_group([sys.executable, "-c", controller], dict(os.environ), timeout=1)
            assert result.returncode == 7
        else:
            with pytest.raises(TimeoutError, match="bounded runtime"):
                fixture._run_owned_group([sys.executable, "-c", controller], dict(os.environ), timeout=0.5)
        assert record.exists()
        assert not psutil.pid_exists(int(record.read_text()))
        assert spawned[0].returncode is not None
        assert spawned[0].stdout.closed
        assert spawned[0].stderr.closed
        with pytest.raises(ChildProcessError):
            os.waitpid(spawned[0].pid, os.WNOHANG)
    finally:
        # A regression must not leave the deliberately stubborn test processes behind.
        for pid in [*([int(record.read_text())] if record.exists() else []), *(p.pid for p in spawned)]:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        for child in spawned:
            child.wait(timeout=2)


def test_denied_process_cleanup_fails_closed_but_reaps_direct_child(monkeypatch):
    child = Mock(pid=90123)
    child.poll.return_value = 7
    process = Mock(pid=90124)
    process.is_running.return_value = True
    process.children.return_value = []
    process.send_signal.side_effect = psutil.AccessDenied(process.pid)
    monkeypatch.setattr(fixture, "_session_members", lambda _: [process])
    monkeypatch.setattr(fixture.os, "killpg", Mock(side_effect=PermissionError("denied")))
    ticks = iter([0, 3, 3, 6])
    monkeypatch.setattr(fixture.time, "monotonic", lambda: next(ticks))
    with pytest.raises(RuntimeError, match="process tree did not stop"):
        fixture._stop_owned_tree(child, {})
    assert process.send_signal.call_args_list == [((signal.SIGTERM,),), ((signal.SIGKILL,),)]
    child.wait.assert_called_once_with(timeout=2)


@pytest.mark.parametrize("operation", ["immediate-setsid-orphan", "setup", "native"])
def test_unprovable_containment_refuses_before_any_spawn(monkeypatch, operation):
    launch = Mock()
    monkeypatch.setattr(fixture.subprocess, "Popen", launch)
    action = fixture._prepare_desktop if operation == "setup" else lambda: fixture._run_owned_group([operation], {})
    with pytest.raises(RuntimeError, match="race-free process containment is unavailable"):
        action()
    launch.assert_not_called()


@pytest.mark.parametrize("failure_site", ["refresh", "session", "identity"])
def test_discovery_failure_still_signals_known_descendant_and_preserves_error(monkeypatch, failure_site):
    child = Mock(pid=90123)
    child.poll.return_value = 7
    process = Mock(pid=90124)
    process.is_running.return_value = True
    process.children.return_value = []
    failure = psutil.AccessDenied(process.pid)
    monkeypatch.setattr(fixture, "_refresh_owned_tree", Mock())
    monkeypatch.setattr(fixture, "_session_members", Mock(return_value=[]))
    if failure_site == "refresh":
        fixture._refresh_owned_tree.side_effect = failure
    elif failure_site == "session":
        fixture._session_members.side_effect = failure
    else:
        process.is_running.side_effect = failure
    monkeypatch.setattr(fixture.os, "killpg", Mock())
    ticks = iter([0, 0, 3, 3, 3, 6])
    monkeypatch.setattr(fixture.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(fixture.time, "sleep", Mock())
    with pytest.raises(RuntimeError, match="could not be verified") as result:
        fixture._stop_owned_tree(child, {process.pid: process})
    assert result.value.__cause__ is failure
    assert process.send_signal.call_args_list == [((signal.SIGTERM,),), ((signal.SIGKILL,),)]
    child.wait.assert_called_once_with(timeout=2)


def test_immediate_orphan_is_not_discoverable_by_session_polling(monkeypatch):
    orphan = Mock(pid=90124)
    monkeypatch.setattr(fixture.psutil, "process_iter", lambda: [orphan])
    # The child already called setsid and its parent exited before the first poll.
    monkeypatch.setattr(fixture.os, "getsid", lambda _: orphan.pid)
    known = {}
    fixture._refresh_owned_tree(90123, known)
    assert known == {}
    orphan.children.assert_not_called()
    launch = Mock()
    monkeypatch.setattr(fixture.subprocess, "Popen", launch)
    with pytest.raises(RuntimeError, match="containment is unavailable"):
        fixture._run_owned_group(
            [sys.executable, "-c", "import os,time; os.fork() and os._exit(0); os.setsid(); time.sleep(60)"],
            {},
        )
    launch.assert_not_called()


def test_discovery_error_is_not_erased_when_known_process_stops(monkeypatch):
    child = Mock(pid=90123)
    child.poll.return_value = 7
    process = Mock(pid=90124)
    process.is_running.return_value = False
    failure = psutil.AccessDenied(90125)
    monkeypatch.setattr(fixture, "_refresh_owned_tree", Mock(side_effect=failure))
    monkeypatch.setattr(fixture, "_session_members", Mock(return_value=[]))
    monkeypatch.setattr(fixture.os, "killpg", Mock())
    with pytest.raises(RuntimeError, match="could not be verified") as result:
        fixture._stop_owned_tree(child, {process.pid: process})
    assert result.value.__cause__ is failure
    process.send_signal.assert_called_once_with(signal.SIGTERM)
    child.wait.assert_called_once_with(timeout=2)
