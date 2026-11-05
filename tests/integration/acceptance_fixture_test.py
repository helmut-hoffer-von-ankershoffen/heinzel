"""The installed-SDK fixture captures registration without reaching a native service manager."""

import functools
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from heinzel.kernel import ProcessOutcome
from heinzel.modules.system import daemon
from heinzel.modules.system.daemon import _service
from tests.fixtures.daemon_manager import record_tool

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("fixture_acceptance", ROOT / "scripts/acceptance/run_acceptance.py")
assert SPEC
assert SPEC.loader
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


@pytest.fixture
def fixture_environment(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for key in ("CONFIG_HOME", "DATA_DIR", "CACHE_DIR", "STATE_DIR", "LOG_DIR"):
        monkeypatch.setenv("HZ_" + key, str(tmp_path / key.lower()))
    monkeypatch.setenv("HZ_SYSTEM_DAEMON__UNIT_DIR", str(tmp_path / "units"))
    binary = tmp_path / "bin"
    binary.mkdir()
    prefix = tmp_path / "tools/heinzel"
    record_tool(prefix, binary)
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(daemon, "DaemonService", functools.partial(daemon.DaemonService, platform="darwin"))
    return binary


def execute_fixture(binary, action, monkeypatch):
    """All native subprocesses are forbidden, including absolute launchctl and systemctl paths."""

    def denied(*_args, **_kwargs):
        msg = "A native subprocess escaped fixture interception"
        raise AssertionError(msg)

    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(sys, "argv", ["fixture", action, str(binary)])
    namespace = {"__name__": "__main__"}
    exec(compile(harness.FIXTURE, "acceptance-fixture", "exec"), namespace)  # noqa: S102 - execute the shipped fixture
    return namespace


def test_registration_capture_generates_real_owned_files_without_native_subprocesses(fixture_environment, monkeypatch):
    state = execute_fixture(fixture_environment, "install", monkeypatch)
    capture, service, unit = state["capture"], state["service"], state["unit"]
    assert capture.calls == [["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(unit)]]
    assert service.owned_unit_state(fixture_environment)
    assert service.paused
    assert any(call[1] == "print" for call in capture.reads)
    assert service.owned_manager_state(fixture_environment).registered
    with pytest.raises(AssertionError, match="Unexpected manager mutation"):
        capture(["/bin/launchctl", "bootout", state["target"]])
    with pytest.raises(AssertionError, match="Unexpected manager mutation"):
        capture(["/bin/launchctl", "bootstrap", "gui/99999", str(unit)])
    with pytest.raises(AssertionError, match="Duplicate registration"):
        capture(capture.calls[0])


def test_trusted_malformed_restored_units_keep_real_quiescence_checks(fixture_environment, monkeypatch):
    installed = execute_fixture(fixture_environment, "install", monkeypatch)
    unit = installed["unit"]
    original = unit.read_bytes()
    queries = []

    def absent_manager(command):
        queries.append(command)
        if command == ["/bin/launchctl", "managername"]:
            return ProcessOutcome(0, "Aqua", "")
        if command == ["/bin/launchctl", "manageruid"]:
            return ProcessOutcome(0, str(os.getuid()), "")
        assert command == ["/bin/launchctl", "list"]
        return ProcessOutcome(0, "PID\tStatus\tLabel\n", "")

    monkeypatch.setattr(_service, "_quiescence_command", absent_manager)
    trusted = execute_fixture(fixture_environment, "check", monkeypatch)
    assert trusted["accepted"] is True
    assert len(queries) == 5
    with pytest.raises(AssertionError, match="quiescence check requested a mutation"):
        trusted["read_only_probe"](["/bin/launchctl", "bootstrap", "gui/1", str(unit)])
    unit.write_bytes(original + b"<!-- altered -->\n")
    assert execute_fixture(fixture_environment, "check", monkeypatch)["accepted"] is False
    unit.write_bytes(original)
    assert execute_fixture(fixture_environment, "check", monkeypatch)["accepted"] is True
    marker = Path(os.environ["HZ_STATE_DIR"]) / "daemon/install.json"
    data = json.loads(marker.read_text())
    data["profile"] = "0" * 64
    marker.write_text(json.dumps(data))
    assert execute_fixture(fixture_environment, "check", monkeypatch)["accepted"] is False


def test_probe_injection_gap_is_stopped_before_any_native_manager_change(fixture_environment, monkeypatch):
    real_service = _service.DaemonService

    def missing_probe(*args, **kwargs):
        kwargs["quiescence_probe"] = None
        return real_service(*args, platform="darwin", **kwargs)

    monkeypatch.setattr(daemon, "DaemonService", missing_probe)
    with pytest.raises(AssertionError, match="Unexpected native subprocess"):
        execute_fixture(fixture_environment, "install", monkeypatch)
