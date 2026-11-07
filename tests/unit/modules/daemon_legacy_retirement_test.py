"""Legacy recovery protocol doubles; no OS manager or real installation is touched."""

import json
import os
from datetime import timedelta
from pathlib import Path

import pytest

from heinzel import Config, HeinzelError, Settings
from heinzel.kernel import DaemonRecoverySelection, ProcessOutcome, private_write
from heinzel.modules.system.daemon import DaemonService, DaemonSettings
from heinzel.modules.system.daemon._legacy_retirement import LegacyRetirement, _file
from heinzel.modules.system.daemon._units import LABEL, launchd_plist, plist_path
from tests.fixtures.daemon_manager import RecordedManager, record_tool

# Every journal step flushes the transaction file and its folder; real flushes made the crash-boundary cases miss
# their 5 s budget on loaded CI runners (stack in os.fsync). The tests that cross boundaries assert the flushes.
pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("recorded_flushes")]


@pytest.fixture
def legacy(tmp_path, monkeypatch, make_kernel):
    # Explicit unit-double fixture authority only; installed proofs must use the genuine installer instead.
    for key in list(os.environ):
        if key.startswith(("HZ_", "XDG_")):
            monkeypatch.delenv(key)
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    settings = Settings()
    binary = home / "bin"
    binary.mkdir()
    prefix = home / "tools/heinzel"
    metadata = prefix / "lib/python3.14/site-packages/heinzel-0.9.15.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: heinzel\nVersion: 0.9.15\n")
    installation = record_tool(prefix, binary)
    manager = RecordedManager(binary)
    service = DaemonService(
        make_kernel(settings_=settings),
        settings,
        DaemonSettings(),
        platform="darwin",
        uid=os.getuid,
        desktop_session=lambda: "Aqua",
        quiescence_installation=installation,
        profile_config=Config.empty(),
    )
    manager.attach(service)
    service._runner = manager

    def probe(command):
        if command[1] == "manageruid":
            return ProcessOutcome(0, f"{os.getuid()}\n", "")
        result = manager.probe(command)
        if command[1] == "print" and result.returncode == 0:
            return ProcessOutcome(
                0,
                f"gui/{os.getuid()}/{LABEL} = {{\n"
                + result.stdout
                + "state = not running\ndefault environment = {\n}\n"
                + f"inherited environment = {{\nHOME => {home}\n}}\n}}\n",
                "",
            )
        return result

    service._quiescence_probe = probe
    unit = plist_path(service.unit_dir)
    unit.parent.mkdir(parents=True, mode=0o700)
    unit.write_text(launchd_plist(binary / "hz", 5, settings.log_dir))
    unit.chmod(0o600)
    private_write(
        service.state_dir / "install.json",
        json.dumps(
            {
                "platform": "darwin",
                "executable": str(binary / "hz"),
                "interval_minutes": 5,
                "installed_at": "2026-11-01T00:00:00+00:00",
                "version": "0.9.15",
            }
        ),
    )
    standard = settings.data_dir / "standard-app.json"
    private_write(standard, '{"format":2,"core_version":"0.9.15","sdk":1,"artifacts":[]}')
    selection = DaemonRecoverySelection(standard, standard.read_bytes())
    return LegacyRetirement(service, selection), service, manager


def test_read_only_plan_binds_real_calendar_bytes_without_granting_historical_profile(legacy):
    recovery, service, manager = legacy
    before = {_file(path) for path in (*service._unit_paths(), service.state_dir / "install.json")}
    plan = recovery.plan()
    assert plan.historical_profile == "unknown"
    assert plan.facts["action"] == "retire-legacy-daemon"
    assert before == {_file(path) for path in (*service._unit_paths(), service.state_dir / "install.json")}
    assert not recovery.root.exists()
    assert not (service.state_dir / "tick.lock").exists()
    assert manager.verbs() == []


def test_confirmed_retirement_preserves_exact_originals_and_replay_never_touches_new_units(legacy):
    recovery, service, manager = legacy
    paths = [*service._unit_paths(), service.state_dir / "install.json"]
    before = [path.read_bytes() for path in paths]
    service.pause()
    plan = recovery.plan()
    assert recovery.retire(plan.digest).status == "retired"
    assert all(not path.exists() for path in paths)
    assert [(recovery.root / plan.digest / f"original-{index}").read_bytes() for index in range(2)] == before
    assert service.paused
    assert not (service.state_dir / "standard-install-pending.json").exists()
    service.install(timedelta(minutes=5), notifier=False)
    installed = [path.read_bytes() for path in paths]
    effects = manager.verbs()
    assert recovery.retire(plan.digest).status == "retired"
    assert [path.read_bytes() for path in paths] == installed
    assert manager.verbs() == effects
    with pytest.raises(HeinzelError):
        recovery.restore_plan(plan.digest)


@pytest.mark.parametrize(
    "change",
    [
        "edited-unit",
        "different-version",
        "changed-marker",
        "alternate-profile",
        "unverified-target",
        "pending",
        "foreign-program",
        "foreign-argv",
        "unknown-home",
        "inherited-profile",
        "active-pid",
        "unknown-state",
        "foreign-label",
        "wrong-uid",
    ],
)
def test_unknown_legacy_evidence_refuses_before_any_mutation(legacy, monkeypatch, change):
    recovery, service, manager = legacy
    unit = service._unit_paths()[0]
    marker = service.state_dir / "install.json"
    if change == "edited-unit":
        unit.write_bytes(unit.read_bytes() + b"\n")
    elif change == "different-version":
        marker.write_text(marker.read_text().replace("0.9.15", "0.9.13"))
    elif change == "changed-marker":
        marker.write_text(marker.read_text()[:-1] + ',"profile":"foreign"}')
    elif change == "alternate-profile":
        monkeypatch.setenv("HZ_CONFIG_HOME", str(Path.home() / "alternate"))
    elif change == "unverified-target":
        (service._quiescence_installation.prefix / "bin/hz").write_text("changed")
    elif change == "pending":
        private_write(service.state_dir / "mutation-pending.json", "{}")
    elif change == "wrong-uid":
        service._uid = lambda: os.getuid() + 1
    else:
        probe = service._quiescence_probe

        def changed(command):
            result = probe(command)
            if command[1] != "print" or result.returncode:
                return result
            replacements = {
                "foreign-program": ("program = ", "program = /foreign/"),
                "foreign-argv": ("tick\n}", "tick\n--extra\n}"),
                "unknown-home": ("HOME => ", "UNOBSERVED => "),
                "inherited-profile": ("inherited environment = {", "inherited environment = {\nHZ_CONFIG => /foreign"),
                "active-pid": ("state = not running", "state = not running\npid = 1234"),
                "unknown-state": ("not running", "running"),
                "foreign-label": ("path = ", "path = /foreign/"),
            }
            return ProcessOutcome(0, result.stdout.replace(*replacements[change]), "")

        service._quiescence_probe = changed
    before = [path.read_bytes() for path in (unit, marker)]
    with pytest.raises(HeinzelError):
        recovery.plan()
    assert [path.read_bytes() for path in (unit, marker)] == before
    assert not recovery.root.exists()
    assert manager.verbs() == []


def test_stale_plan_refuses_without_creating_transaction(legacy):
    recovery, service, manager = legacy
    plan = recovery.plan()
    marker = service.state_dir / "install.json"
    marker.write_text(marker.read_text().replace("00:00:00", "00:00:01"))
    with pytest.raises(HeinzelError):
        recovery.retire(plan.digest)
    assert not recovery.root.exists()
    assert manager.verbs() == []


@pytest.mark.parametrize(
    "boundary", ["intent", "unload-intent", "unloaded", "move-0-intent", "moved-0", "move-1-intent", "moved-1"]
)
def test_interruption_keeps_gate_and_requires_new_digest_confirmed_restore(legacy, monkeypatch, boundary):
    recovery, service, manager = legacy
    plan = recovery.plan()
    before = [path.read_bytes() for path in (*service._unit_paths(), service.state_dir / "install.json")]
    write = recovery._write

    def interrupted(journal):
        write(journal)
        if journal["state"] == boundary:
            message = "injected crash after durable boundary"
            raise RuntimeError(message)

    monkeypatch.setattr(recovery, "_write", interrupted)
    with pytest.raises(RuntimeError):
        recovery.retire(plan.digest)
    assert (service.state_dir / "standard-install-pending.json").exists() == (boundary != "intent")
    effects = manager.verbs()
    with pytest.raises(HeinzelError):
        recovery.retire(plan.digest)
    assert manager.verbs() == effects
    monkeypatch.setattr(recovery, "_write", write)
    restored = recovery.restore_plan(plan.digest)
    with pytest.raises(HeinzelError):
        recovery.restore(plan.digest, "0" * 64)
    assert recovery.restore(plan.digest, restored.digest).status == "restored"
    assert [path.read_bytes() for path in (*service._unit_paths(), service.state_dir / "install.json")] == before
    assert not (service.state_dir / "standard-install-pending.json").exists()
    assert manager.registered is True


def test_stopped_registration_retires_without_manager_mutation_and_keeps_pause(legacy):
    recovery, service, manager = legacy
    manager.registered = False
    service.pause()
    plan = recovery.plan()
    assert recovery.retire(plan.digest).status == "retired"
    assert service.paused
    assert manager.verbs() == []


def test_terminal_crash_only_finishes_its_exact_gate_without_repeating_unload(legacy, monkeypatch):
    recovery, service, manager = legacy
    plan = recovery.plan()
    write = recovery._write

    def interrupted(journal):
        write(journal)
        if journal["state"] == "retired":
            message = "injected crash before own gate cleanup"
            raise RuntimeError(message)

    monkeypatch.setattr(recovery, "_write", interrupted)
    with pytest.raises(RuntimeError):
        recovery.retire(plan.digest)
    effects = manager.verbs()
    monkeypatch.setattr(recovery, "_write", write)
    assert recovery.retire(plan.digest).status == "retired"
    assert manager.verbs() == effects
    assert not (service.state_dir / "standard-install-pending.json").exists()


def test_lost_unload_ack_retains_gate_and_never_retries_without_new_restore_consent(legacy):
    recovery, service, manager = legacy
    plan = recovery.plan()
    probe = service._quiescence_probe

    def lost_ack(command):
        outcome = probe(command)
        return ProcessOutcome(None, "", "") if command[1] == "bootout" else outcome

    service._quiescence_probe = lost_ack
    with pytest.raises(HeinzelError):
        recovery.retire(plan.digest)
    assert manager.registered is False
    assert all(path.exists() for path in (*service._unit_paths(), service.state_dir / "install.json"))
    effects = manager.verbs()
    with pytest.raises(HeinzelError):
        recovery.retire(plan.digest)
    assert manager.verbs() == effects
    service._quiescence_probe = probe
    restore = recovery.restore_plan(plan.digest)
    assert recovery.restore(plan.digest, restore.digest).status == "restored"
    assert manager.registered is True


@pytest.mark.parametrize("drift", ["target", "backup", "foreign-unit", "profile", "foreign-manager", "gate"])
def test_restore_refuses_drift_without_overwriting_foreign_state(legacy, monkeypatch, drift):
    recovery, service, manager = legacy
    plan = recovery.plan()
    write = recovery._write

    def interrupted(journal):
        write(journal)
        if journal["state"] == "moved-0":
            message = "injected interruption"
            raise RuntimeError(message)

    monkeypatch.setattr(recovery, "_write", interrupted)
    with pytest.raises(RuntimeError):
        recovery.retire(plan.digest)
    monkeypatch.setattr(recovery, "_write", write)
    backup = recovery.root / plan.digest / "original-0"
    if drift == "target":
        (service._quiescence_installation.prefix / "bin/hz").write_text("new generation")
    elif drift == "backup":
        backup.write_bytes(backup.read_bytes() + b"edited")
    elif drift == "foreign-unit":
        service._unit_paths()[0].write_text("foreign")
    elif drift == "profile":
        monkeypatch.setenv("HZ_CONFIG", str(Path.home() / "another.toml"))
    elif drift == "gate":
        private_write(service.state_dir / "standard-install-pending.json", '{"token":"foreign"}')
    else:
        manager.registered = True
        manager.loaded_data["ProgramArguments"][0] = "/foreign"
    effects = manager.verbs()
    before = {
        p: p.read_bytes() for p in (backup, *service._unit_paths(), service.state_dir / "install.json") if p.exists()
    }
    with pytest.raises(HeinzelError):
        recovery.restore_plan(plan.digest)
    assert manager.verbs() == effects
    assert {p: p.read_bytes() for p in before} == before


def native_account(monkeypatch, home, *, gid=20):
    from types import SimpleNamespace

    from heinzel.modules.system.daemon import _legacy_retirement as implementation

    account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=gid, pw_dir=str(home))
    monkeypatch.setattr(implementation.pwd, "getpwuid", lambda uid: account)


def without_home(service):
    probe = service._quiescence_probe

    def absent_home(command):
        result = probe(command)
        return ProcessOutcome(result.returncode, result.stdout.replace(f"HOME => {Path.home()}\n", ""), result.stderr)

    service._quiescence_probe = absent_home


def test_complete_missing_home_plan_discloses_native_current_selection_never_historical_ownership(legacy, monkeypatch):
    recovery, service, manager = legacy
    native_account(monkeypatch, Path.home())
    without_home(service)
    plan = recovery.plan()
    routing = plan.model_dump()["facts"]["manager"]["routing"]
    assert routing["home"] == "unobserved"
    assert routing["selection"] == "native-account-default"
    assert routing["historical_profile"] == "unknown"
    assert "only this exact registration" in routing["disclosure"]
    assert manager.verbs() == []
    assert recovery.retire(plan.digest).status == "retired"


def test_missing_home_never_authorizes_different_native_home(legacy, monkeypatch):
    recovery, service, manager = legacy
    native_account(monkeypatch, Path.home() / "different")
    without_home(service)
    with pytest.raises(HeinzelError):
        recovery.plan()
    assert not recovery.root.exists()
    assert manager.verbs() == []


def test_changed_native_account_after_plan_refuses_before_any_retirement_effect(legacy, monkeypatch):
    recovery, service, manager = legacy
    native_account(monkeypatch, Path.home())
    without_home(service)
    plan = recovery.plan()
    native_account(monkeypatch, Path.home(), gid=21)
    with pytest.raises(HeinzelError):
        recovery.retire(plan.digest)
    assert not recovery.root.exists()
    assert manager.verbs() == []


@pytest.mark.parametrize(
    "drift",
    ["missing-block", "duplicate-block", "unknown-block", "unclosed-block", "duplicate-key", "hidden-home", "python"],
)
def test_incomplete_or_conflicting_environment_never_qualifies_legacy_registration(legacy, drift):
    recovery, service, manager = legacy
    probe = service._quiescence_probe

    def changed(command):
        outcome = probe(command)
        if command[1] != "print":
            return outcome
        replacements = {
            "missing-block": ("default environment = {\n}\n", ""),
            "duplicate-block": ("default environment = {\n}", "default environment = {\n}\ndefault environment = {\n}"),
            "unknown-block": ("default environment", "other environment"),
            "unclosed-block": ("default environment = {\n}", "default environment = {\n"),
            "duplicate-key": ("inherited environment = {", f"inherited environment = {{\nHOME => {Path.home()}"),
            "hidden-home": ("default environment = {", "default environment = {\nHOME => /foreign"),
            "python": ("default environment = {", "default environment = {\nPYTHONHOME => /foreign"),
        }
        return ProcessOutcome(0, outcome.stdout.replace(*replacements[drift]), "")

    service._quiescence_probe = changed
    with pytest.raises(HeinzelError):
        recovery.plan()
    assert manager.verbs() == []
    assert not recovery.root.exists()


def test_recognized_014_calendar_record_can_be_retired_without_inventing_profile(legacy):
    recovery, service, _manager = legacy
    marker = service.state_dir / "install.json"
    marker.write_text(marker.read_text().replace("0.9.15", "0.9.14"))
    plan = recovery.plan()
    assert recovery.retire(plan.digest).status == "retired"
    assert json.loads((recovery.root / plan.digest / "original-1").read_text())["version"] == "0.9.14"


def test_ancient_notifierless_template_is_not_misrepresented_as_014_or_015(legacy):
    import plistlib

    recovery, service, manager = legacy
    unit = service._unit_paths()[0]
    value = plistlib.loads(unit.read_bytes())
    del value["AssociatedBundleIdentifiers"]
    unit.write_bytes(plistlib.dumps(value, sort_keys=True))
    before = unit.read_bytes()
    with pytest.raises(HeinzelError):
        recovery.plan()
    assert unit.read_bytes() == before
    assert manager.verbs() == []
    assert not recovery.root.exists()


@pytest.fixture
def completed_history(legacy):
    """Synthetic receipt protocol for unit negatives only; the installed test must generate genuine history."""
    from datetime import datetime, timedelta

    recovery, service, manager = legacy
    marker = service.state_dir / "install.json"
    old_signature = service._handover_signature()
    record = json.loads(marker.read_text())
    record["handover_id"] = "a" * 64
    private_write(marker, json.dumps(record, indent=2) + "\n")
    request = {
        "request_id": record["handover_id"],
        "boot": "00000000-0000-0000-0000-000000000001",
        "unit_signature": old_signature,
        "birth_ticks": 1,
        "birth_hz": 0,
        "birth_ns_dev": 0,
        "birth_ns_ino": 0,
    }
    private_write(service.state_dir / "handover-request.json", json.dumps(request))
    tick = (datetime.now().astimezone() - timedelta(seconds=1)).isoformat()
    observation = {
        "request_id": request["request_id"],
        "version": "0.9.15",
        "fingerprint": service._quiescence_installation.startup_fingerprint(),
        "contributions": [],
        "tick_at": tick,
        "old_unit_signature": old_signature,
        "new_unit_signature": service._handover_signature(),
    }
    private_write(service.state_dir / "handover-observation.json", json.dumps(observation))
    private_write(
        service.state_dir / "heartbeat.json",
        json.dumps(
            {
                "last_tick": tick,
                "completed_at": tick,
                "version": "0.9.15",
                "source": "scheduled",
                "outcome": "completed",
                "interval_minutes": 5,
            }
        ),
    )
    return recovery, service, manager


def test_completed_history_is_archived_as_an_explicit_separate_effect_with_evidence_copies(completed_history):
    recovery, service, manager = completed_history
    request = service.state_dir / "handover-request.json"
    observation = service.state_dir / "handover-observation.json"
    heartbeat = service.state_dir / "heartbeat.json"
    before = {path: path.read_bytes() for path in (request, observation, heartbeat)}
    plan = recovery.plan()
    assert len(plan.facts["files"]) == 3
    assert "archive verified completed handover request" in str(plan.facts["effects"])
    assert recovery.retire(plan.digest).status == "retired"
    assert not request.exists()
    backup = recovery.root / plan.digest
    assert (backup / "original-2").read_bytes() == before[request]
    assert (backup / "evidence-0").read_bytes() == before[observation] == observation.read_bytes()
    assert (backup / "evidence-1").read_bytes() == before[heartbeat] == heartbeat.read_bytes()
    private_write(request, '{"request_id":"a later unrelated request"}')
    private_write(service.state_dir / "standard-install-pending.json", '{"token":"a later unrelated gate"}')
    effects = manager.verbs()
    assert recovery.retire(plan.digest).status == "retired"
    assert request.read_text() == '{"request_id":"a later unrelated request"}'
    assert manager.verbs() == effects


@pytest.mark.parametrize(
    "drift",
    [
        "request",
        "old-signature",
        "new-signature",
        "fingerprint",
        "version",
        "manual-heartbeat",
        "uncovered-heartbeat",
        "torn-observation",
        "pending",
    ],
)
def test_unknown_or_torn_handover_history_refuses_without_archiving_anything(completed_history, drift):
    recovery, service, manager = completed_history
    request = service.state_dir / "handover-request.json"
    observation = service.state_dir / "handover-observation.json"
    heartbeat = service.state_dir / "heartbeat.json"
    if drift == "request":
        request.write_text(request.read_text().replace("a" * 64, "c" * 64))
    elif drift == "torn-observation":
        observation.write_text('{"request_id":')
    elif drift == "pending":
        private_write(service.state_dir / "handover-pending.json", "{}")
    else:
        replacements = {
            "old-signature": (observation, "old_unit_signature", "c" * 64),
            "new-signature": (observation, "new_unit_signature", "c" * 64),
            "fingerprint": (observation, "fingerprint", "c" * 64),
            "version": (observation, "version", "0.9.14"),
            "manual-heartbeat": (heartbeat, "source", "manual"),
            "uncovered-heartbeat": (heartbeat, "last_tick", "2020-01-01T00:00:00+00:00"),
        }
        path, key, value = replacements[drift]
        data = json.loads(path.read_text())
        data[key] = value
        path.write_text(json.dumps(data))
    before = {
        path: path.read_bytes()
        for path in (*service._unit_paths(), service.state_dir / "install.json", request, observation, heartbeat)
    }
    with pytest.raises(HeinzelError):
        recovery.plan()
    assert {path: path.read_bytes() for path in before} == before
    assert not recovery.root.exists()
    assert manager.verbs() == []


@pytest.mark.parametrize(
    "boundary", ["unload-intent", "move-0-intent", "moved-0", "move-1-intent", "moved-1", "move-2-intent", "moved-2"]
)
def test_completed_history_interruption_restores_exact_coherent_three_file_set(
    completed_history, monkeypatch, boundary, recorded_flushes
):
    recovery, service, manager = completed_history
    plan = recovery.plan()
    paths = (*service._unit_paths(), service.state_dir / "install.json", service.state_dir / "handover-request.json")
    before = [path.read_bytes() for path in paths]
    write = recovery._write

    def interrupted(journal):
        write(journal)
        if journal["state"] == boundary:
            message = "injected crash in completed-history retirement"
            raise RuntimeError(message)

    monkeypatch.setattr(recovery, "_write", interrupted)
    flushed = len(recorded_flushes)
    with pytest.raises(RuntimeError):
        recovery.retire(plan.digest)
    assert len(recorded_flushes) > flushed, "the journal step before the crash was flushed"
    monkeypatch.setattr(recovery, "_write", write)
    restore = recovery.restore_plan(plan.digest)
    flushed = len(recorded_flushes)
    assert recovery.restore(plan.digest, restore.digest).status == "restored"
    assert len(recorded_flushes) > flushed, "the restore was flushed"
    assert [path.read_bytes() for path in paths] == before
    assert manager.registered
    assert not (service.state_dir / "standard-install-pending.json").exists()


def test_changed_prepared_backup_refuses_before_unloading_registration(legacy, monkeypatch):
    recovery, service, manager = legacy
    plan = recovery.plan()
    paths = (*service._unit_paths(), service.state_dir / "install.json")
    before = [path.read_bytes() for path in paths]
    write = recovery._write

    def changed(journal):
        write(journal)
        if journal["state"] == "unload-intent":
            (recovery.root / plan.digest / "original-0").write_text("foreign edited backup")

    monkeypatch.setattr(recovery, "_write", changed)
    with pytest.raises(HeinzelError):
        recovery.retire(plan.digest)
    assert manager.verbs() == []
    assert [path.read_bytes() for path in paths] == before


@pytest.mark.parametrize("retired_boundary", ["unloaded", "moved-2"])
@pytest.mark.parametrize("boundary", ["restore-intent", "restore-load-intent"])
@pytest.mark.parametrize("drift", ["unit", "marker", "request", "gate", "backup"])
def test_restore_durable_boundary_drift_never_loads_changed_state(
    completed_history, monkeypatch, retired_boundary, boundary, drift
):
    recovery, service, manager = completed_history
    plan = recovery.plan()
    write = recovery._write

    def interrupted(journal):
        write(journal)
        if journal["state"] == retired_boundary:
            msg = "injected interruption at retirement boundary"
            raise RuntimeError(msg)

    monkeypatch.setattr(recovery, "_write", interrupted)
    with pytest.raises(RuntimeError):
        recovery.retire(plan.digest)
    monkeypatch.setattr(recovery, "_write", write)
    restore = recovery.restore_plan(plan.digest)
    path = {
        "unit": service._unit_paths()[0],
        "marker": service.state_dir / "install.json",
        "request": service.state_dir / "handover-request.json",
        "gate": service.state_dir / "standard-install-pending.json",
        "backup": recovery.root / plan.digest / "original-0",
    }[drift]
    foreign = '{"token":"foreign"}' if drift == "gate" else "foreign changed bytes"

    def changed(journal):
        write(journal)
        if journal["state"] == boundary:
            path.write_text(foreign)

    monkeypatch.setattr(recovery, "_write", changed)
    effects = manager.verbs()
    with pytest.raises(HeinzelError):
        recovery.restore(plan.digest, restore.digest)
    assert manager.verbs() == effects
    assert not manager.registered
    assert path.read_text() == foreign
    assert (service.state_dir / "standard-install-pending.json").exists()


@pytest.mark.parametrize("foreign", ["regular", "symlink"])
def test_restore_exclusive_rename_preserves_destination_appearing_after_admission(legacy, monkeypatch, foreign):
    from heinzel.modules.system.daemon import _legacy_retirement as implementation

    recovery, service, manager = legacy
    plan = recovery.plan()
    write = recovery._write

    def interrupted(journal):
        write(journal)
        if journal["state"] == "moved-0":
            message = "injected interruption with missing unit"
            raise RuntimeError(message)

    monkeypatch.setattr(recovery, "_write", interrupted)
    with pytest.raises(RuntimeError):
        recovery.retire(plan.digest)
    monkeypatch.setattr(recovery, "_write", write)
    restore = recovery.restore_plan(plan.digest)
    target = service.state_dir / "foreign-data"
    target.write_text("foreign bytes")
    unit = service._unit_paths()[0]
    backup = recovery.root / plan.digest / "original-0"
    restore_files = recovery._restore_files
    absent = implementation._absent
    file = implementation._file
    injecting = checked_absent = appeared = False

    def restoring(*args):
        nonlocal injecting
        injecting = True
        return restore_files(*args)

    def checked(path):
        nonlocal checked_absent
        result = absent(path)
        if injecting and path == unit and result:
            checked_absent = True
        return result

    def read(path):
        nonlocal appeared
        captured = file(path)
        if injecting and checked_absent and path == backup and not appeared:
            appeared = True
            if foreign == "regular":
                unit.write_text("foreign bytes")
            else:
                unit.symlink_to(target)
        return captured

    monkeypatch.setattr(recovery, "_restore_files", restoring)
    monkeypatch.setattr(implementation, "_absent", checked)
    monkeypatch.setattr(implementation, "_file", read)
    effects = manager.verbs()
    with pytest.raises(FileExistsError):
        recovery.restore(plan.digest, restore.digest)
    assert manager.verbs() == effects
    assert service._unit_paths()[0].read_text() == "foreign bytes"
    assert service._unit_paths()[0].is_symlink() == (foreign == "symlink")
    assert (recovery.root / plan.digest / "original-0").exists()
    assert (service.state_dir / "standard-install-pending.json").exists()


def test_restore_crash_after_atomic_no_clobber_move_remains_explicitly_recoverable(legacy, monkeypatch):
    from heinzel.modules.system.daemon import _legacy_retirement as implementation

    recovery, service, manager = legacy
    plan = recovery.plan()
    write = recovery._write

    def interrupted(journal):
        write(journal)
        if journal["state"] == "moved-1":
            message = "injected interruption with both original files absent"
            raise RuntimeError(message)

    monkeypatch.setattr(recovery, "_write", interrupted)
    with pytest.raises(RuntimeError):
        recovery.retire(plan.digest)
    monkeypatch.setattr(recovery, "_write", write)
    restore = recovery.restore_plan(plan.digest)
    rename = implementation._rename_exclusive

    def lost_ack(source, destination):
        rename(source, destination)
        message = "injected crash after the exclusive move"
        raise RuntimeError(message)

    monkeypatch.setattr(implementation, "_rename_exclusive", lost_ack)
    effects = manager.verbs()
    with pytest.raises(RuntimeError):
        recovery.restore(plan.digest, restore.digest)
    assert manager.verbs() == effects
    assert (service.state_dir / "standard-install-pending.json").exists()
    monkeypatch.setattr(implementation, "_rename_exclusive", rename)
    renewed = recovery.restore_plan(plan.digest)
    assert recovery.restore(plan.digest, renewed.digest).status == "restored"
    assert manager.registered


def test_legacy_recovery_refuses_linux_before_manager_or_file_mutations(legacy, monkeypatch):
    recovery, service, manager = legacy
    paths = [*service._unit_paths(), service.state_dir / "install.json"]
    before = {path: path.read_bytes() for path in paths}
    monkeypatch.setattr(service, "_platform", "linux")
    with pytest.raises(HeinzelError, match="unknown or changed"):
        recovery.plan()
    assert {path: path.read_bytes() for path in paths} == before
    assert manager.verbs() == []
    assert not recovery.root.exists()
    assert not (service.state_dir / "standard-install-pending.json").exists()


def native_print_fixture(service):
    """Preserve native launchctl structure, substituting only fictional test identities."""
    import plistlib

    from heinzel.modules.system.daemon._units import PATH

    executable = Path(plistlib.loads(service._unit_paths()[0].read_bytes())["ProgramArguments"][0])
    output = (Path(__file__).parents[2] / "fixtures/daemon_legacy_launchctl.txt").read_text()
    for key, value in {
        "@UID@": str(os.getuid()),
        "@UNIT@": str(service._unit_paths()[0]),
        "@EXECUTABLE@": str(executable),
        "@HOME@": str(Path.home()),
        "@PATH@": f"{executable.parent}:{PATH}",
    }.items():
        output = output.replace(key, value)
    return output


def test_native_coalition_states_do_not_override_the_job_state(legacy, monkeypatch):
    recovery, service, manager = legacy
    native_account(monkeypatch, Path.home())
    output = native_print_fixture(service)
    assert output.count("state = active") == 2
    assert output.count("state = not running") == 1
    original = service._quiescence_probe

    def native_structure(command):
        result = original(command)
        return ProcessOutcome(0, output, "") if command[1] == "print" and result.returncode == 0 else result

    service._quiescence_probe = native_structure
    plan = recovery.plan()
    assert plan.historical_profile == "unknown"
    assert not recovery.root.exists()
    assert manager.verbs() == []


@pytest.mark.parametrize(
    "drift",
    [
        "duplicate-state",
        "duplicate-path",
        "duplicate-program",
        "duplicate-arguments",
        "duplicate-environment",
        "running",
        "active-pid",
        "duplicate-pid",
        "hidden-state",
        "unbalanced",
        "outside-root",
        "wrong-root",
    ],
)
def test_native_top_level_conflicts_refuse_before_recovery_effects(legacy, monkeypatch, drift):
    recovery, service, manager = legacy
    native_account(monkeypatch, Path.home())
    output = native_print_fixture(service)
    replacements = {
        "duplicate-state": ("\tstate = not running", "\tstate = not running\n\tstate = not running"),
        "duplicate-path": ("\ttype = LaunchAgent", "\tpath = /foreign\n\ttype = LaunchAgent"),
        "duplicate-program": ("\ttype = LaunchAgent", "\tprogram = /foreign\n\ttype = LaunchAgent"),
        "duplicate-arguments": ("\ttype = LaunchAgent", "\targuments = {\n\t}\n\ttype = LaunchAgent"),
        "duplicate-environment": ("\ttype = LaunchAgent", "\tenvironment = {\n\t}\n\ttype = LaunchAgent"),
        "running": ("\tstate = not running", "\tstate = running"),
        "active-pid": ("\tstate = not running", "\tstate = not running\n\tpid = 1234"),
        "duplicate-pid": ("\tstate = not running", "\tstate = not running\n\tpid = 0\n\tpid = 0"),
        "hidden-state": ("\tstate = not running", ""),
        "unbalanced": ("\tresource coalition = {", "\tresource coalition = {\n\t\textra = {"),
        "outside-root": ("\tstate = not running", "}\n\tstate = not running\nextra = {"),
        "wrong-root": (f"gui/{os.getuid()}/", f"gui/{os.getuid() + 1}/"),
    }
    output = output.replace(*replacements[drift])
    original = service._quiescence_probe

    def native_structure(command):
        result = original(command)
        return ProcessOutcome(0, output, "") if command[1] == "print" and result.returncode == 0 else result

    service._quiescence_probe = native_structure
    before = [path.read_bytes() for path in (*service._unit_paths(), service.state_dir / "install.json")]
    with pytest.raises(HeinzelError):
        recovery.plan()
    assert [path.read_bytes() for path in (*service._unit_paths(), service.state_dir / "install.json")] == before
    assert not recovery.root.exists()
    assert manager.verbs() == []


def test_excessive_native_manager_nesting_refuses():
    from heinzel.modules.system.daemon._legacy_retirement import MAX_MANAGER_DEPTH, _launchd_fields

    output = f"gui/{os.getuid()}/{LABEL} = {{\n"
    output += "nested = {\n" * MAX_MANAGER_DEPTH
    output += "}\n" * (MAX_MANAGER_DEPTH + 1)
    with pytest.raises(HeinzelError):
        _launchd_fields(output)
