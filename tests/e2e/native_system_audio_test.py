"""Capture-free hosted preflight for a future native system-audio study."""

import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

pytestmark = pytest.mark.e2e
REPO = Path(__file__).resolve().parents[2]


def _session_members(session):
    """Enumerate the isolated session even after its controller has exited."""
    members = []
    for process in psutil.process_iter():
        try:
            if os.getsid(process.pid) == session:
                members.append(process)
        except (ProcessLookupError, psutil.NoSuchProcess):
            continue
    return members


def _refresh_owned_tree(session, known):
    """Keep process identities across parent exits and detached child groups."""
    for process in [*known.values(), *_session_members(session)]:
        if process.is_running():
            known[process.pid] = process
            try:
                for descendant in process.children(recursive=True):
                    known[descendant.pid] = descendant
            except psutil.NoSuchProcess:
                pass


def _signal_owned_tree(child, known, requested, errors):
    """Try both group and identity-checked process signals before checking exit."""
    try:
        _refresh_owned_tree(child.pid, known)
    except (OSError, psutil.Error) as error:
        # Discovery failure must not prevent cleanup of identities already retained.
        errors.append(error)
    try:
        os.killpg(child.pid, requested)
    except ProcessLookupError:
        pass
    except PermissionError:
        # Per-identity signaling below remains available when group signaling is denied.
        pass
    # A detached group, or a denied killpg, still needs per-process termination.
    for process in reversed(list(known.values())):
        try:
            process.send_signal(requested)
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied as error:
            errors.append(error)


def _remaining_owned(session, known, errors):
    """Retain inaccessible identities as alive and preserve discovery errors."""
    remaining = []
    for process in known.values():
        try:
            if process.is_running():
                remaining.append(process)
        except psutil.Error as error:
            errors.append(error)
            remaining.append(process)
    try:
        remaining.extend(_session_members(session))
    except (OSError, psutil.Error) as error:
        errors.append(error)
    return remaining


def _stop_owned_tree(child, known):
    """Attempt all known cleanup even when discovery fails; retain that failure."""
    errors = []
    stopped = False
    try:
        for requested in (signal.SIGTERM, signal.SIGKILL):
            _signal_owned_tree(child, known, requested, errors)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                child.poll()
                remaining = _remaining_owned(child.pid, known, errors)
                if not remaining:
                    stopped = True
                    break
                time.sleep(0.02)
            if stopped:
                break
    finally:
        # Direct-child failure must not replace an earlier discovery failure.
        try:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired) as error:
            errors.append(error)
    if errors or not stopped:
        message = "Owned native test process tree did not stop or could not be verified"
        raise RuntimeError(message) from (errors[0] if errors else None)


def _require_process_containment():
    """Fail before spawn: polling cannot own a child that immediately orphans."""
    # setsid + parent exit can happen between any two process snapshots. A new
    # session and remembered psutil identities do not close that race on macOS.
    # Keep setup and native launch disabled until an OS-enforced ownership
    # boundary can be established before any child executes.
    message = "Native preflight disabled: race-free process containment is unavailable"
    raise RuntimeError(message)


def _run_owned_group(command, environment, *, timeout=90):
    """Own setup and controller descendants through launch errors and timeouts."""
    _require_process_containment()
    child = subprocess.Popen(
        command,
        cwd=REPO,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    known = {}
    try:
        deadline = time.monotonic() + timeout
        while True:
            _refresh_owned_tree(child.pid, known)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                message = "Owned native test exceeded its bounded runtime"
                raise TimeoutError(message)
            try:
                stdout, stderr = child.communicate(timeout=min(0.05, remaining))
                return subprocess.CompletedProcess(command, child.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    finally:
        try:
            _stop_owned_tree(child, known)
        finally:
            child.stdout.close()
            child.stderr.close()


def _prepare_desktop():
    """Provision the locked runtime explicitly; pnpm may disable install scripts."""
    commands = (
        ["pnpm", "--dir", "../desktop", "install", "--frozen-lockfile"],
        ["pnpm", "--dir", "../desktop", "exec", "node", "node_modules/electron/install.js"],
        ["pnpm", "--dir", "../desktop", "exec", "node", "-p", 'require("electron")'],
    )
    environment = dict(os.environ)
    environment.pop("ELECTRON_SKIP_BINARY_DOWNLOAD", None)
    for command in commands:
        setup = _run_owned_group([sys.executable, "scripts/web_build.py", *command], environment, timeout=180)
        assert setup.returncode == 0, setup.stderr


def test_native_system_audio_capture_free_preflight():
    """Only a disposable macOS ARM host may launch the isolated native fixture."""
    if (
        os.environ.get("CI") != "true"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or os.environ.get("FOCUSED_SELECTOR")
        != "tests/e2e/native_system_audio_test.py::test_native_system_audio_capture_free_preflight"
        or platform.system() != "Darwin"
    ):
        pytest.skip("requires explicitly selected disposable macOS hosted CI")
    assert platform.machine() == "arm64"
    _prepare_desktop()
    environment = {**os.environ, "HZ_DISPOSABLE_NATIVE_CI": "1"}
    result = _run_owned_group(
        [sys.executable, "scripts/web_build.py", "exec", "node", "../desktop/e2e/system-audio-preflight.mjs"],
        environment,
    )
    assert result.returncode == 0, result.stderr
    assert "no media capture requested" in result.stdout
    assert "isolated profile removed" in result.stdout
