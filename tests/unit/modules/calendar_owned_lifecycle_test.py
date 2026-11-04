"""All reminder profiles join offline ownership, fencing and copied recovery without provider discovery."""

import hashlib
import os
import plistlib
import shlex
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from heinzel import HeinzelError
from heinzel.kernel import (
    HostServiceManager,
    MaintenanceLocks,
    host_maintenance_gate,
    host_maintenance_lock,
    private_write,
)
from heinzel.kernel._offline_uv import VerifiedUV
from heinzel.kernel._tool_uninstall import owned_file
from heinzel.kernel._uninstall_journal import RemovalCapture, RemovalJournal
from heinzel.modules.org import _reminder_service as supervisor
from heinzel.modules.org._reminder_maintenance import CalendarMaintenance
from tests.unit.kernel.installation_continuity_test import tool
from tests.unit.offline_uninstall_recovery_test import copy_module, journal, successful_uv
from tests.unit.offline_uninstall_test import removable

__all__ = ["journal", "removable", "tool"]
pytestmark = pytest.mark.unit


class Manager(HostServiceManager):
    def __init__(self, descriptors):
        self.units = {item.name: item for item in descriptors}
        self.active = set(self.units)
        self.commands = []

    def command(self, arguments):
        self.commands.append(arguments)
        if arguments[0] == "/usr/bin/systemctl":
            return self.linux(arguments)
        operation = arguments[1]
        if operation in {"managername", "manageruid"}:
            text = "Aqua\n" if operation == "managername" else f"{os.getuid()}\n"
            return subprocess.CompletedProcess(arguments, 0, text, "")
        if operation == "list":
            return subprocess.CompletedProcess(
                arguments, 0, "PID\tStatus\tLabel\n" + "".join(f"123 0 {name}\n" for name in sorted(self.active)), ""
            )
        name = arguments[-1].rsplit("/", 1)[-1]
        if operation == "bootout":
            self.active.remove(name)
            return subprocess.CompletedProcess(arguments, 0, "", "")
        if operation == "print" and name in self.active:
            item = self.units[name]
            text = (
                f"path = {item.unit}\nprogram = {item.program}\nworking directory = {item.directory}\narguments = {{\n"
                + "\n".join(item.arguments)
                + "\n}\nenvironment = {\n"
                + "\n".join(f"{key} => {value}" for key, value in item.environment.items())
                + "\n}\npid = 123\n"
            )
            return subprocess.CompletedProcess(arguments, 0, text, "")
        return subprocess.CompletedProcess(arguments, 113, "", "Could not find service")

    def linux(self, arguments):
        operation = arguments[2]
        if operation == "list-units":
            text = "".join(f"{name} loaded inactive dead fixture\n" for name in sorted(set(self.units) | self.active))
            return subprocess.CompletedProcess(arguments, 0, text, "")
        if operation == "daemon-reload":
            return subprocess.CompletedProcess(arguments, 0, "", "")
        name = arguments[3] if operation == "show" else arguments[-1]
        item = self.units.get(name)
        if operation == "disable":
            self.active.discard(name)
            return subprocess.CompletedProcess(arguments, 0, "", "")
        if operation == "is-enabled":
            return subprocess.CompletedProcess(arguments, 1, "disabled\n", "")
        if item is None:
            text = f"Id={name}\nLoadState=not-found\nMainPID=0\nControlPID=0\nJob=\n"
        else:
            environment = shlex.join([f"{key}={value}" for key, value in item.environment.items()])
            text = (
                f"Id={name}\nLoadState=loaded\nFragmentPath={item.unit}\nDropInPaths=\n"
                f"ExecStart={{ path={item.program} ; argv[]={' '.join(item.arguments)} ; ignore_errors=no ; }}\n"
                f"Environment={environment}\nWorkingDirectory={item.directory}\n"
                f"ActiveState={'active' if name in self.active else 'inactive'}\n"
                f"UnitFileState={'enabled' if name in self.active else 'disabled'}\n"
                f"MainPID={123 if name in self.active else 0}\nControlPID=0\nJob=\n"
            )
        return subprocess.CompletedProcess(arguments, 0, text, "")


@pytest.fixture(params=["darwin", "linux"])
def calendar(removable, tmp_path, monkeypatch, request):
    platform = request.param
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(supervisor, "_installation", lambda: removable)
    _, binary = removable.tool_destination()
    profiles = []
    for name in ("one", "two"):
        base = home / name
        environment = {"HZ_CONFIG_HOME": str(base / "config"), "HZ_CONFIG": ""}
        environment.update(
            {f"HZ_{field.upper()}_DIR": str(base / field) for field in ("state", "data", "cache", "log")}
        )
        profile = supervisor.Profile(environment)
        path = supervisor.path_for(platform, home, profile)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(supervisor.definition(platform, binary / "hz", profile))
        data = base / "data/retained"
        data.parent.mkdir(parents=True, mode=0o700)
        data.write_text("consent/replay/user data retained")
        profiles.append(profile)
    owner = CalendarMaintenance()
    owner.platform = platform
    absent = Manager(())
    descriptors = owner.inventory(removable.prefix, binary, absent)
    manager = Manager(descriptors)
    return removable, owner, descriptors, manager, profiles


def test_every_running_profile_stops_before_definition_cleanup_and_sdk_removal(calendar):
    installation, owner, descriptors, manager, profiles = calendar
    paths = tuple(path for item in descriptors for path in item.locks)
    assert len(descriptors) == 2
    with MaintenanceLocks(paths) as locks:
        plans = [owner.guard(item, manager, locks) for item in descriptors]
        for plan in plans:
            plan.stop()
        assert not manager.active
        assert all(item.unit.exists() for item in descriptors)
        for plan in plans:
            with pytest.raises(HeinzelError):
                plan.finish()
        shutil.rmtree(installation.prefix)
        for plan in plans:
            plan.finish()
    assert all(not item.unit.exists() for item in descriptors)
    assert all((Path(profile.environment["HZ_DATA_DIR"]) / "retained").read_text() for profile in profiles)
    stops = [cmd for cmd in manager.commands if "bootout" in cmd or "disable" in cmd]
    assert len(stops) == 2


@pytest.mark.parametrize("change", ["unit", "profile", "foreign-loaded", "data-in-sdk"])
def test_foreign_or_changed_calendar_inventory_has_no_effects(calendar, change):
    installation, owner, descriptors, manager, _ = calendar
    item = descriptors[0]
    if change == "foreign-loaded":
        manager.active.add(
            supervisor.LABEL + "." + "f" * 24
            if item.platform == "darwin"
            else "hz-calendar-reminders-" + "f" * 24 + ".service"
        )
    elif item.platform == "linux":
        item.unit.write_text(item.unit.read_text().replace(str(item.program), "/foreign/hz"))
    else:
        value = plistlib.loads(item.unit.read_bytes())
        if change == "unit":
            value["ProgramArguments"] = ["/foreign/hz", "org", "calendar-reminder-worker"]
        elif change == "profile":
            value["EnvironmentVariables"]["HZ_STATE_DIR"] = "/foreign/state"
        else:
            value["EnvironmentVariables"]["HZ_DATA_DIR"] = str(installation.prefix / "user-data")
        item.unit.write_bytes(plistlib.dumps(value))
    with pytest.raises((HeinzelError, ValueError)):
        owner.inventory(installation.prefix, installation.tool_destination()[1], manager)
    assert not any(cmd[1] == "bootout" for cmd in manager.commands)
    assert installation.prefix.exists()
    assert all(item.unit.exists() for item in descriptors)


def test_concurrent_setup_and_worker_cycles_cannot_pass_the_sdk_fence(calendar):
    installation, _, descriptors, _, profiles = calendar
    with (
        MaintenanceLocks([host_maintenance_lock(installation.prefix)]),
        pytest.raises(HeinzelError, match="progress"),
        supervisor.lifecycle(profiles[0]),
    ):
        pytest.fail("A concurrent setup/worker must not enter")
    private_write(host_maintenance_gate(installation.prefix), '{"token":"another-operation"}')
    for profile in profiles:
        with pytest.raises(HeinzelError, match="maintenance"), supervisor.lifecycle(profile):
            pytest.fail("A pending gate must deny every recorded profile")
    assert all(item.unit.exists() for item in descriptors)


def test_copied_recovery_with_sdk_absent_cleans_all_profiles_and_refuses_loaded_worker(calendar, tmp_path, monkeypatch):
    installation, _, descriptors, manager, profiles = calendar
    directory = tmp_path / "recovery"
    directory.mkdir(mode=0o700)
    uv = directory / "uv"
    uv.write_bytes(installation.test_remover.path.read_bytes())
    uv.chmod(0o500)
    captured = owned_file(uv)
    executable = VerifiedUV(uv, hashlib.sha256(captured.content).hexdigest(), captured.identity)
    state = tmp_path / "state"
    paths = (
        *(path for item in descriptors for path in item.locks),
        state / "ui/lifecycle.lock",
        state / "daemon/lifecycle.lock",
        state / "daemon/tick.lock",
    )
    from heinzel.kernel import _uninstall_journal as journal_module

    monkeypatch.setattr(journal_module, "sys", SimpleNamespace(platform=descriptors[0].platform))
    with MaintenanceLocks(paths):
        record = RemovalJournal(
            executable,
            prefix=installation.prefix,
            binary=installation.tool_destination()[1],
            state=state,
            captured=RemovalCapture(
                tuple(file for item in descriptors for file in item.files),
                installation.tool_entrypoints(),
                {item.name: item.manager_facts() for item in descriptors} if descriptors[0].platform == "linux" else {},
                gate=host_maintenance_gate(installation.prefix),
                locks=paths,
                labels=tuple(item.name for item in descriptors),
                integrations=descriptors,
            ),
        )
        record.begin()
    assert [item["name"] for item in record.value["integrations"]] == [item.name for item in descriptors]
    assert all(item["arguments"][-1] == "calendar-reminder-worker" for item in record.value["integrations"])
    assert all(item["files"][0]["content"] for item in record.value["integrations"])
    module = copy_module(record)
    monkeypatch.setattr(module, "_linux_identity", lambda: (1, 2, 3))
    monkeypatch.setattr(module, "_manager", lambda command, platform: manager.command(command).stdout)
    calls = []
    monkeypatch.setattr(module.subprocess, "run", successful_uv(installation, calls))
    with pytest.raises(ValueError, match=r"still loaded|not proven stopped"):
        module.recover(record.directory, module.EXPECTED_AUTHORITY)
    assert calls == []
    assert all(item.unit.exists() for item in descriptors)
    for path in installation.tool_entrypoints():
        path.unlink()
    shutil.rmtree(installation.prefix)
    manager.active.clear()
    record.phase("removed")
    outcome = module.recover(record.directory, module.EXPECTED_AUTHORITY)
    assert outcome["phase"] == "complete"
    assert calls == []
    assert all(not item.unit.exists() for item in descriptors)
    assert not record.gate.exists()
    assert all((Path(profile.environment["HZ_DATA_DIR"]) / "retained").exists() for profile in profiles)


def test_recovery_rejects_replaced_union_lock_before_manager_or_native_effect(journal, monkeypatch):
    record, installation, unit = journal
    path = Path(record.value["locks"][0])
    saved = path.with_suffix(".retained")
    path.rename(saved)
    path.write_text("")
    path.chmod(0o600)
    module = copy_module(record)
    monkeypatch.setattr(
        module, "managers_absent", lambda *_: pytest.fail("Changed locks cannot authorize manager effects")
    )
    with pytest.raises(ValueError, match="lock ownership changed"):
        module.recover(record.directory, module.EXPECTED_AUTHORITY)
    assert saved.exists()
    assert path.exists()
    assert installation.prefix.exists()
    assert unit.exists()
    assert record.gate.exists()


def test_disabled_owner_is_inventoried_without_resolving_accounts_or_external_plugins(monkeypatch):
    from heinzel.kernel import Credentials, Kernel, Settings, _discovery, boot
    from heinzel.modules.system._uninstall import _contributions

    def forbidden(*args, **kwargs):
        pytest.fail("Maintenance inventory must not access external plugins, credentials or providers")

    monkeypatch.setattr(_discovery, "entry_points", forbidden)
    monkeypatch.setattr(Credentials, "__init__", forbidden)
    original = Kernel.resolve
    kinds = []

    def resolve(kernel, kind):
        kinds.append(kind)
        assert kind is CalendarMaintenance
        return original(kernel, kind)

    monkeypatch.setattr(Kernel, "resolve", resolve)
    settings = Settings(disabled_modules=["org", "system.daemon"])
    ordinary = boot(settings, builtin_only=True)
    assert all(record.qualname != "org" for record in ordinary.modules)
    maintenance = boot(settings, builtin_only=True, maintenance=True)
    assert len(_contributions(maintenance)) == 1
    assert kinds == [CalendarMaintenance]
    assert settings.disabled_modules == frozenset({"org", "system.daemon"})


@pytest.mark.parametrize("change", ["foreign", "rebound"])
def test_host_inventory_refuses_foreign_or_rebound_contribution(change):
    from dataclasses import replace

    from heinzel.kernel import boot
    from heinzel.modules.system._uninstall import _contributions

    kernel = boot(builtin_only=True, maintenance=True)
    if change == "foreign":
        record = kernel._records["org"]
        kernel._records["org"] = replace(record, origin=replace(record.origin, builtin=False))
    else:
        kernel.bind(CalendarMaintenance, CalendarMaintenance())
    with pytest.raises(HeinzelError, match="expected built-in"):
        _contributions(kernel)


def test_a_foreign_gate_appearing_before_begin_cannot_authorize_any_effect(journal, monkeypatch):
    from types import SimpleNamespace

    from heinzel.modules.system._uninstall import _apply_removal

    record, installation, unit = journal
    foreign = '{"token":"another-transaction"}\n'
    private_write(record.gate, foreign)
    effects = []
    service = SimpleNamespace(stop=lambda *args: effects.append("stop"), finish=lambda: effects.append("finish"))
    monkeypatch.setattr(installation, "uninstall_offline", lambda **kwargs: effects.append("uv"))
    with pytest.raises(HeinzelError, match="needs inspection"):
        _apply_removal(
            installation,
            installation.startup_fingerprint(),
            installation.test_remover,
            (service, service, record, (), lambda: None),
            None,
        )
    assert effects == []
    assert record.gate.read_text() == foreign
    assert installation.prefix.exists()
    assert unit.exists()
