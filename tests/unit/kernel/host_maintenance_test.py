"""Exact owned native stop settlement, without weakening foreign-manager refusal."""

import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from heinzel import HeinzelError
from heinzel.kernel import _host_maintenance
from heinzel.kernel._host_maintenance import (
    HostIntegration,
    HostIntegrationRemoval,
    HostServiceManager,
    MaintenanceLocks,
)
from heinzel.kernel._tool_uninstall import owned_file

pytestmark = pytest.mark.unit


def test_maintenance_refuses_lock_path_replaced_after_flock(tmp_path, monkeypatch):
    path = tmp_path / "lifecycle.lock"
    displaced = tmp_path / "displaced.lock"
    original = _host_maintenance.private_lock

    @contextmanager
    def replace_after_lock(candidate):
        with original(candidate) as retained:
            candidate.rename(displaced)
            candidate.write_bytes(b"substitute")
            candidate.chmod(0o600)
            yield retained

    monkeypatch.setattr(_host_maintenance, "private_lock", replace_after_lock)
    with pytest.raises(HeinzelError, match="not held"), MaintenanceLocks((path,)):
        pytest.fail("The replacement was mistaken for the flocked descriptor")
    assert path.read_bytes() == b"substitute"
    assert displaced.read_bytes() == b""


class Manager(HostServiceManager):
    def __init__(self, integration):
        self.integration = integration
        self.commands = []
        self.stopping = False
        self.elapsed = 0.0
        self.settle_at = 0.2
        self.foreign_after_stop = False
        self.edit_after_stop = False
        self.slow_observation = False

    def command(self, arguments):
        self.commands.append(arguments)
        item = self.integration
        if arguments[1] == "bootout":
            self.stopping = True
            return subprocess.CompletedProcess(arguments, 0, "", "")
        absent = self.stopping and self.elapsed >= self.settle_at
        if arguments[1] == "list":
            text = "PID\tStatus\tLabel\n" + ("" if absent else f"123\t0\t{item.name}\n")
            return subprocess.CompletedProcess(arguments, 0, text, "")
        if self.stopping and self.slow_observation:
            self.elapsed += 6
            absent = True
        if absent:
            return subprocess.CompletedProcess(arguments, 1, "", "absent")
        program = "/foreign" if self.stopping and self.foreign_after_stop else str(item.program)
        text = (
            f"path = {item.unit}\nprogram = {program}\narguments = {{\n"
            + "\n".join(item.arguments)
            + f"\n}}\nworking directory = {item.directory}\npid = 123\n"
        )
        return subprocess.CompletedProcess(arguments, 0, text, "")

    def sleep(self, duration):
        self.elapsed += duration
        if self.edit_after_stop:
            self.integration.unit.write_text("changed definition")


@pytest.fixture
def native_stop(tmp_path):
    unit = tmp_path / "owned.plist"
    unit.write_text("exact owned definition")
    integration = HostIntegration(
        name="test.example.owned",
        prefix=tmp_path / "prefix",
        platform="darwin",
        files=(owned_file(unit),),
        locks=(tmp_path / "lifecycle.lock",),
        unit=unit,
        program=Path("/example/python"),
        arguments=("/example/python", "worker"),
        environment={},
        directory=tmp_path,
    )
    manager = Manager(integration)
    with MaintenanceLocks(integration.locks) as locks:
        yield (
            HostIntegrationRemoval(integration, manager, locks, clock=lambda: manager.elapsed, sleep=manager.sleep),
            manager,
            unit,
        )


def test_owned_native_stop_waits_for_actual_manager_absence_and_keeps_definition(native_stop):
    removal, manager, unit = native_stop
    removal.stop()
    assert manager.elapsed >= manager.settle_at
    assert unit.read_text() == "exact owned definition"
    assert sum(command[1] == "bootout" for command in manager.commands) == 1
    removal.quiescent()


def test_still_loaded_job_refuses_at_the_existing_web_stop_budget(native_stop):
    removal, manager, unit = native_stop
    manager.settle_at = 100
    with pytest.raises(HeinzelError):
        removal.stop()
    assert manager.elapsed == 5.0
    assert unit.exists()


@pytest.mark.parametrize("problem", ["foreign_after_stop", "edit_after_stop"])
def test_changed_identity_during_stop_refuses_without_waiting_out_the_budget(native_stop, problem):
    removal, manager, unit = native_stop
    setattr(manager, problem, True)
    with pytest.raises(HeinzelError):
        removal.stop()
    assert manager.elapsed < 5.0
    assert unit.exists()


def test_late_absence_is_not_accepted_as_timely_stop_evidence(native_stop):
    removal, manager, unit = native_stop
    manager.slow_observation = True
    with pytest.raises(HeinzelError):
        removal.stop()
    assert unit.exists()
