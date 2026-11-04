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


def _change_after_journal(change, descriptors, manager):
    manager.active.clear()
    if change == "unknown-worker":
        manager.active.add(
            supervisor.LABEL + "." + "f" * 24
            if descriptors[0].platform == "darwin"
            else "hz-calendar-reminders-" + "f" * 24 + ".service"
        )
    elif change == "extra-definition":
        original = descriptors[0].unit
        prefix = supervisor.LABEL + "." if original.suffix == ".plist" else "hz-calendar-reminders-"
        extra = original.with_name(prefix + "f" * 24 + original.suffix)
        extra.write_bytes(original.read_bytes())
    else:
        descriptors[0].unit.write_text("changed definition after journal capture")


def _calendar_record(calendar, tmp_path, monkeypatch, *, compatibility=False, begin=True):
    installation, owner, descriptors, _manager, _profiles = calendar
    directory = tmp_path / "recovery"
    directory.mkdir(mode=0o700)
    uv = directory / "uv"
    uv.write_bytes(installation.test_remover.path.read_bytes())
    uv.chmod(0o500)
    captured = owned_file(uv)
    executable = VerifiedUV(uv, hashlib.sha256(captured.content).hexdigest(), captured.identity)
    state = tmp_path / "state"
    paths = (
        host_maintenance_lock(installation.prefix),
        *(path for item in descriptors for path in item.locks),
        state / "ui/lifecycle.lock",
        state / "daemon/lifecycle.lock",
        state / "daemon/tick.lock",
    )
    from heinzel.kernel import _uninstall_journal as journal_module

    monkeypatch.setattr(journal_module, "sys", SimpleNamespace(platform=owner.platform))
    with MaintenanceLocks(paths):
        record = RemovalJournal(
            executable,
            prefix=installation.prefix,
            binary=installation.tool_destination()[1],
            state=state,
            captured=RemovalCapture(
                tuple(file for item in descriptors for file in item.files),
                installation.tool_entrypoints(),
                {item.name: item.manager_facts() for item in descriptors} if owner.platform == "linux" else {},
                gate=host_maintenance_gate(installation.prefix),
                locks=paths,
                labels=tuple(item.name for item in descriptors),
                integrations=descriptors,
                scopes=owner.discovery_scopes(),
                compatibility_gates=(state / "daemon/standard-install-pending.json",) if compatibility else (),
            ),
        )
        if begin:
            record.begin()
    return record


@pytest.mark.parametrize("late_change", [None, "unknown-worker", "extra-definition", "changed-definition"])
def test_copied_recovery_with_sdk_absent_cleans_all_profiles_and_refuses_loaded_worker(
    calendar, tmp_path, monkeypatch, late_change
):
    installation, _owner, descriptors, manager, profiles = calendar
    record = _calendar_record(calendar, tmp_path, monkeypatch)
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
    if late_change:
        _change_after_journal(late_change, descriptors, manager)
        with pytest.raises(ValueError, match=r"inventory|scope|changed|still loaded"):
            module.recover(record.directory, module.EXPECTED_AUTHORITY)
        assert calls == []
        assert installation.prefix.exists()
        assert all(item.unit.exists() for item in descriptors)
        assert record.gate.exists()
        return
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


def test_gate_publication_refuses_a_redirected_parent_before_writing(journal):
    record, installation, unit = journal
    record.gate.unlink()
    parent = record.gate.parent
    saved = parent.with_name(parent.name + "-retained")
    parent.rename(saved)
    parent.symlink_to(saved, target_is_directory=True)
    with pytest.raises(HeinzelError, match="physically owned"):
        record.begin()
    assert not (saved / record.gate.name).exists()
    assert installation.prefix.exists()
    assert unit.exists()


@pytest.mark.parametrize("change", ["replace", "remove"])
def test_gate_change_at_durable_removing_boundary_has_no_native_or_cleanup_effect(journal, monkeypatch, change):
    from heinzel.modules.system._uninstall import _apply_removal

    record, installation, unit = journal
    record.gate.unlink()
    foreign = '{"token":"foreign-at-removing"}\n'
    original = record.phase
    effects = []

    def phase(value):
        original(value)
        if value == "removing":
            if change == "replace":
                private_write(record.gate, foreign)
            else:
                record.gate.unlink()

    monkeypatch.setattr(record, "phase", phase)
    monkeypatch.setattr(installation, "uninstall_offline", lambda **kwargs: effects.append("uv"))
    service = SimpleNamespace(stop=lambda *args: None, finish=lambda: effects.append("cleanup"))
    with pytest.raises(HeinzelError, match="needs inspection"):
        _apply_removal(
            installation,
            installation.startup_fingerprint(),
            installation.test_remover,
            (service, service, record, (), lambda: None),
            None,
        )
    assert effects == []
    assert installation.prefix.exists()
    assert unit.exists()
    if change == "replace":
        assert record.gate.read_text() == foreign
    else:
        assert not record.gate.exists()


@pytest.mark.parametrize("late_change", ["unknown-worker", "extra-definition", "replaced-directory"])
@pytest.mark.parametrize("initial_directory", ["empty", "absent"])
def test_empty_calendar_scope_remains_complete_in_copied_recovery(
    calendar, tmp_path, monkeypatch, late_change, initial_directory
):
    installation, owner, descriptors, manager, profiles = calendar
    directory = descriptors[0].unit.parent
    for item in descriptors:
        item.unit.unlink()
    manager.units.clear()
    manager.active.clear()
    if initial_directory == "absent":
        directory.rmdir()
    record = _calendar_record((installation, owner, (), manager, profiles), tmp_path, monkeypatch)
    scope = record.value["discovery_scopes"][0]
    assert scope["identity"] is None if initial_directory == "absent" else scope["identity"]
    if late_change == "unknown-worker":
        manager.active.add(
            owner.discovery_scopes()[0].manager_prefix + "f" * 24 + (".service" if owner.platform == "linux" else "")
        )
    elif late_change == "extra-definition":
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        (directory / (owner.discovery_scopes()[0].file_prefix + "foreign")).write_text("unrecorded definition")
    else:
        if directory.exists():
            directory.rename(directory.with_name(directory.name + "-retained"))
        directory.mkdir(mode=0o700)
    module = copy_module(record)
    monkeypatch.setattr(module, "_linux_identity", lambda: (1, 2, 3))
    monkeypatch.setattr(module, "_manager", lambda command, platform: manager.command(command).stdout)
    calls = []
    monkeypatch.setattr(module.subprocess, "run", successful_uv(installation, calls))
    with pytest.raises(ValueError, match=r"inventory|scope|directory changed|still loaded"):
        module.recover(record.directory, module.EXPECTED_AUTHORITY)
    assert calls == []
    assert installation.prefix.exists()
    assert record.gate.exists()
    assert directory.exists() or initial_directory == "absent"
    assert all((Path(profile.environment["HZ_DATA_DIR"]) / "retained").exists() for profile in profiles)


@pytest.mark.parametrize("late_change", ["unknown-worker", "extra-definition"])
def test_post_removal_recovery_retains_all_files_when_namespace_grows(calendar, tmp_path, monkeypatch, late_change):
    installation, _owner, descriptors, manager, _profiles = calendar
    record = _calendar_record(calendar, tmp_path, monkeypatch)
    for path in installation.tool_entrypoints():
        path.unlink()
    shutil.rmtree(installation.prefix)
    record.phase("removed")
    _change_after_journal(late_change, descriptors, manager)
    module = copy_module(record)
    monkeypatch.setattr(module, "_linux_identity", lambda: (1, 2, 3))
    monkeypatch.setattr(module, "_manager", lambda command, platform: manager.command(command).stdout)
    calls = []
    monkeypatch.setattr(module.subprocess, "run", successful_uv(installation, calls))
    with pytest.raises(ValueError, match=r"inventory|scope|still loaded"):
        module.recover(record.directory, module.EXPECTED_AUTHORITY)
    assert calls == []
    assert all(item.unit.exists() for item in descriptors)
    assert record.gate.exists()


def copied_calendar_recovery(record, manager, monkeypatch, calls, installation):
    module = copy_module(record)
    monkeypatch.setattr(module, "_linux_identity", lambda: (1, 2, 3))
    monkeypatch.setattr(module, "_manager", lambda command, platform: manager.command(command).stdout)
    monkeypatch.setattr(module.subprocess, "run", successful_uv(installation, calls))
    return module


def test_historical_compatibility_gate_is_exclusive_before_either_fence_is_published(calendar, tmp_path, monkeypatch):
    installation, _owner, descriptors, _manager, _profiles = calendar
    record = _calendar_record(calendar, tmp_path, monkeypatch, compatibility=True, begin=False)
    compatibility = Path(record.value["compatibility_gates"][0]["path"])
    private_write(compatibility, "foreign operation")
    with pytest.raises(FileExistsError):
        record.begin()
    assert not record.gate.exists()
    assert compatibility.read_text() == "foreign operation"
    assert installation.prefix.exists()
    assert all(item.unit.exists() for item in descriptors)


@pytest.mark.parametrize("change", ["foreign", "missing"])
def test_changed_historical_fence_after_stopped_refuses_native_removal_and_cleanup(
    calendar, tmp_path, monkeypatch, change
):
    installation, _owner, descriptors, manager, _profiles = calendar
    record = _calendar_record(calendar, tmp_path, monkeypatch, compatibility=True)
    record.phase("stopped")
    manager.active.clear()
    compatibility = Path(record.value["compatibility_gates"][0]["path"])
    if change == "foreign":
        private_write(compatibility, "foreign operation")
    else:
        compatibility.unlink()
    calls = []
    module = copied_calendar_recovery(record, manager, monkeypatch, calls, installation)
    with pytest.raises((ValueError, OSError)):
        module.recover(record.directory, module.EXPECTED_AUTHORITY)
    assert calls == []
    assert installation.prefix.exists()
    assert all(item.unit.exists() for item in descriptors)
    assert record.gate.exists()
    assert compatibility.read_text() == "foreign operation" if change == "foreign" else not compatibility.exists()


def test_prepared_partial_fence_publication_revalidates_and_completes_only_its_exact_missing_gate(
    calendar, tmp_path, monkeypatch
):
    from heinzel.kernel import _uninstall_journal as journals

    installation, _owner, descriptors, manager, _profiles = calendar
    record = _calendar_record(calendar, tmp_path, monkeypatch, compatibility=True, begin=False)
    compatibility = Path(record.value["compatibility_gates"][0]["path"])
    original = journals.recovery.publish_gate

    def interrupt(value, path):
        if path == compatibility:
            msg = "publication interrupted"
            raise OSError(msg)
        original(value, path)

    monkeypatch.setattr(journals.recovery, "publish_gate", interrupt)
    with pytest.raises(OSError, match="publication interrupted"):
        record.begin()
    monkeypatch.setattr(journals.recovery, "publish_gate", original)
    assert record.gate.exists()
    assert not compatibility.exists()
    calls = []
    module = copied_calendar_recovery(record, manager, monkeypatch, calls, installation)
    with pytest.raises(ValueError, match=r"still loaded|not proven stopped"):
        module.recover(record.directory, module.EXPECTED_AUTHORITY)
    assert calls == []
    assert not compatibility.exists()
    manager.active.clear()
    outcome = module.recover(record.directory, module.EXPECTED_AUTHORITY)
    assert outcome["phase"] == "complete"
    assert len(calls) == 1
    assert not installation.prefix.exists()
    assert all(not item.unit.exists() for item in descriptors)
    assert not compatibility.exists()
    assert not record.gate.exists()


@pytest.mark.parametrize("foreign", [False, True])
def test_terminal_paired_gate_cleanup_with_sdk_absent_never_repeats_target_effects(
    calendar, tmp_path, monkeypatch, foreign
):
    installation, _owner, descriptors, manager, _profiles = calendar
    record = _calendar_record(calendar, tmp_path, monkeypatch, compatibility=True)
    compatibility = Path(record.value["compatibility_gates"][0]["path"])
    for path in installation.tool_entrypoints():
        path.unlink()
    shutil.rmtree(installation.prefix)
    for item in descriptors:
        item.unit.unlink()
    record.phase("cleaned")
    manager.active.clear()
    if foreign:
        private_write(compatibility, "foreign terminal operation")
    else:
        compatibility.unlink()
    calls = []
    module = copied_calendar_recovery(record, manager, monkeypatch, calls, installation)
    monkeypatch.setattr(module, "_owned_unlink", lambda *_: pytest.fail("No target cleanup remains"))
    if foreign:
        with pytest.raises(ValueError, match="gate changed"):
            module.recover(record.directory, module.EXPECTED_AUTHORITY)
        assert record.gate.exists()
        assert compatibility.read_text() == "foreign terminal operation"
    else:
        assert module.recover(record.directory, module.EXPECTED_AUTHORITY)["phase"] == "complete"
        assert not record.gate.exists()
        assert not compatibility.exists()
    assert calls == []
