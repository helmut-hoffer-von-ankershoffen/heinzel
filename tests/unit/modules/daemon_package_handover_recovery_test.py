"""Exercise a real mutation fence, checkpoint, exclusive archive and tick with a manager protocol double."""

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from heinzel import HeinzelError
from heinzel.heads.web._auto_update import AutoUpdater
from heinzel.kernel import DaemonRecoverySelection, ProcessOutcome, private_write
from heinzel.modules.system._announced import Announcement
from heinzel.modules.system._update import UpdateService
from heinzel.modules.system.daemon import DaemonService, DaemonSettings, _installation_lifecycle, _service
from heinzel.modules.system.daemon import _package_handover_recovery as recovery_module
from heinzel.modules.system.daemon._runtime import lock
from tests.fixtures.daemon_manager import RecordedManager, record_tool

pytestmark = pytest.mark.unit


@pytest.fixture
def interrupted(tmp_path, monkeypatch, settings, make_kernel, request):
    binary = tmp_path / "bin"
    binary.mkdir()
    prefix = tmp_path / "tools/heinzel"
    installation = record_tool(prefix, binary)
    interpreter = tmp_path / "fixture-python"
    interpreter.write_text("must never execute")
    interpreter.chmod(0o700)
    (prefix / "bin/python").symlink_to(interpreter)
    metadata = prefix / "lib/python3.14/site-packages/heinzel-0.9.21.dist-info/METADATA"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("Metadata-Version: 2.1\nName: heinzel\nVersion: 0.9.21\n")
    installation.record_startup_state(expected=installation.startup_fingerprint())
    manager = RecordedManager(binary)

    def probe(command):
        if command[1] == "manageruid":
            return ProcessOutcome(0, f"{os.getuid()}\n", "")
        result = manager.probe(command)
        # launchd accepts the work but the 0.9.22 observer times out, before the manager replies.
        return ProcessOutcome(None, "", "") if command[1] == "kickstart" else result

    birth = {"ticks": 10, "hz": 0, "ns_dev": 0, "ns_ino": 0}
    service = DaemonService(
        make_kernel(),
        settings,
        DaemonSettings(unit_dir=tmp_path / "units"),
        runner=manager,
        platform="darwin",
        uid=os.getuid,
        which=lambda name: f"/bin/{name}",
        desktop_session=lambda: "Aqua",
        quiescence_installation=installation,
        quiescence_probe=probe,
        continuity_fingerprint=installation.startup_fingerprint,
        boot_identity=lambda: "same-boot",
        birth_fence=birth.copy,
        process_birth=birth.copy,
    )
    manager.attach(service)
    monkeypatch.setattr(_service, "__version__", "0.9.21")
    service.install(timedelta(minutes=5), notifier=False)
    manager.pid = os.getpid()
    service.tick()
    manager.pid = None
    marker = service.state_dir / "mutation-pending.json"
    handover = service.state_dir / "handover-request.json"
    automatic = not getattr(request, "param", False)
    failure = _leave_failed_update((service, installation, metadata, handover), monkeypatch, automatic=automatic)
    incident = tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in (marker, handover, failure))
    standard = settings.data_dir / "standard-app.json"
    private_write(standard, "verified standard selection")
    selection = DaemonRecoverySelection(standard, standard.read_bytes())
    birth["ticks"] += 1
    manager.commands.clear()
    return recovery_module.PackageHandoverRecovery(service, selection), service, manager, marker, handover, incident


def _leave_failed_update(fixture, monkeypatch, *, automatic):
    service, installation, metadata, request = fixture
    folder = service.state_dir.parent / "auto-update"
    update = UpdateService(installation, object(), lifecycle=_installation_lifecycle(service))
    attempted = []

    def replace_packages(*_args):
        metadata.write_text("Metadata-Version: 2.1\nName: heinzel\nVersion: 0.9.22\n")
        metadata.parent.rename(metadata.parent.with_name("heinzel-0.9.22.dist-info"))
        monkeypatch.setattr(_service, "__version__", "0.9.22")
        if _args:
            receipt = installation.prefix / "uv-receipt.toml"
            receipt.write_text(receipt.read_text() + 'constraints = [{name="heinzel",specifier="==0.9.22"}]\n')
        return installation.startup_fingerprint()

    def trial(version):
        assert (folder / "before-update").is_dir()
        assert installation.installed_core_version() == version == "0.9.22"
        attempted.append(version)
        return True

    monkeypatch.setattr(update, "_install_announced", replace_packages)

    def apply(news):
        return update.apply_announced(
            Announcement(core=news["core"], plugins={}), current="0.9.21", folder=folder, trial=trial
        )

    if not automatic:
        folder.mkdir()
        with pytest.raises(HeinzelError, match="manager request is uncertain"):
            _manual_update(service, installation, replace_packages)

    def clock():
        if not request.exists():
            return datetime(2026, 11, 6, 11, 8, tzinfo=UTC)
        seconds = request.stat().st_mtime_ns / 1_000_000_000
        return datetime.fromtimestamp(seconds, UTC) + timedelta(seconds=5.010)

    auto = AutoUpdater(
        service._kernel,
        restart=lambda: pytest.fail("old web restarted"),
        version="0.9.21",
        update=apply,
        development=None,
        clock=clock,
    )
    auto.plan.core = "0.9.22"
    auto.apply()
    assert attempted == (["0.9.22"] if automatic else [])
    assert not (folder / "before-update").exists()
    assert auto.plan.retry_at == clock() + timedelta(hours=6)
    file_time = request.stat().st_mtime_ns + 5_020_000_000
    failure = service.state_dir.parent / "web/auto-update.json"
    os.utime(failure, ns=(file_time, file_time))
    return failure


def _manual_update(service, installation, replace_packages):
    # A manual package mutation can leave an indistinguishable request, then the automatic attempt is refused.
    with service.mutation_guard(installation.startup_state_matches, installation.rollback_identity) as scope:
        plan = scope.capture()
        generation = replace_packages()
        installation.record_startup_state(expected=generation)
        scope.verified_complete(generation)
        scope.request(plan)


def test_archives_only_verified_mutation_and_waits_for_real_tick(interrupted):
    recovery, service, manager, marker, request, incident = interrupted
    heartbeat = service.state_dir / "heartbeat.json"
    before_heartbeat = heartbeat.read_bytes()
    before_request = request.read_bytes()
    token = marker.read_bytes()
    assert service.tick().locked
    assert heartbeat.read_bytes() == before_heartbeat
    assert service.status().version_mismatch
    plan = recovery.plan(incident)
    assert plan["status"] == "confirmation-required"
    assert marker.read_bytes() == token
    assert not Path(plan["archive"]).exists()
    outcome = recovery.resume(incident, plan["digest"])
    assert outcome["status"] == "awaiting-scheduled-tick"
    assert not marker.exists()
    assert Path(outcome["archive"]).read_bytes() == token
    assert request.read_bytes() == before_request
    assert heartbeat.read_bytes() == before_heartbeat
    assert not manager.verbs()
    assert service.status().version_mismatch
    manager.pid = os.getpid()
    assert not service.tick().locked
    assert service.status().version == "0.9.22"
    assert not service.status().version_mismatch
    assert service.handover_observation(json.loads(before_request)["request_id"]) is not None
    with pytest.raises(FileNotFoundError):
        recovery.resume(incident, plan["digest"])


@pytest.mark.parametrize(
    "problem", ["wrong-version", "old-request", "snapshot", "snapshot-link", "new-heartbeat", "active", "foreign"]
)
def test_ambiguous_state_preserves_both_original_records(interrupted, problem):
    recovery, service, manager, marker, request, incident = interrupted
    if problem == "wrong-version":
        target = service.recovery_installation
        metadata = next(target.prefix.glob("lib/python*/site-packages/*/METADATA"))
        metadata.write_text(metadata.read_text().replace("0.9.22", "0.9.23"))
        target.record_startup_state(expected=target.startup_fingerprint())
    elif problem == "old-request":
        earlier = marker.stat().st_mtime_ns - 1
        os.utime(request, ns=(earlier, earlier))
    elif problem == "snapshot":
        (service.state_dir.parent / "auto-update/before-update").mkdir(parents=True)
    elif problem == "snapshot-link":
        snapshots = service.state_dir.parent / "auto-update"
        snapshots.rmdir()
        snapshots.symlink_to(service.state_dir)
    elif problem == "new-heartbeat":
        (service.state_dir / "heartbeat.json").touch()
    elif problem == "active":
        manager.pid = os.getpid()
    else:
        manager.loaded_data["EnvironmentVariables"]["HZ_STATE_DIR"] = "/foreign"
    before = marker.read_bytes(), request.read_bytes()
    with pytest.raises(HeinzelError):
        recovery.plan(incident)
    assert (marker.read_bytes(), request.read_bytes()) == before
    assert not manager.verbs()


def test_drift_or_concurrent_update_refuses_confirmation(interrupted):
    recovery, service, _, marker, _, incident = interrupted
    plan = recovery.plan(incident)
    web = service.state_dir.parent / "web"
    with lock(web / "auto-update-apply.lock") as acquired:
        assert acquired
        with pytest.raises(HeinzelError):
            recovery.resume(incident, plan["digest"])
    assert marker.exists()
    path = web / "auto-update.json"
    path.write_text(path.read_text().replace("6 hours", "4 hours"))
    with pytest.raises(HeinzelError):
        recovery.resume(incident, plan["digest"])
    assert marker.exists()


@pytest.mark.parametrize("after_move", [False, True])
def test_interruption_cannot_clear_two_markers_or_lose_original_evidence(interrupted, monkeypatch, after_move):
    recovery, _, _, marker, request, incident = interrupted
    plan = recovery.plan(incident)
    before = marker.read_bytes(), request.read_bytes()
    actual_move = recovery_module.rename_exclusive

    def interrupted_move(source, destination, *, expected_source):
        if after_move:
            actual_move(source, destination, expected_source=expected_source)
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(recovery_module, "rename_exclusive", interrupted_move)
        with pytest.raises(KeyboardInterrupt):
            recovery.resume(incident, plan["digest"])
    archive = Path(plan["archive"])
    assert request.read_bytes() == before[1]
    if after_move:
        assert not marker.exists()
        assert archive.read_bytes() == before[0]
        with pytest.raises(FileNotFoundError):
            recovery.resume(incident, plan["digest"])
    else:
        assert not archive.exists()
        assert marker.read_bytes() == before[0]
        assert recovery.resume(incident, plan["digest"])["status"] == "awaiting-scheduled-tick"


@pytest.mark.parametrize(
    ("command_parts", "elapsed", "explicit_deadline", "accepted"),
    [
        (("kickstart", _service.LABEL), 5.010, None, True),
        (("kickstart", _service.LABEL), 10.010, None, True),
        (("kickstart", _service.LABEL), 15.010, None, False),
        (("kickstart", _service.LABEL), 5.010, 5.0, False),
        (("kickstart", "foreign"), 5.010, None, False),
        (("print", _service.LABEL), 5.010, None, False),
    ],
)
def test_launchd_throttle_keeps_only_owned_kickstart_within_its_deadline(
    monkeypatch, command_parts, elapsed, explicit_deadline, accepted
):
    elapsed_now = 0.0
    verb, label = command_parts
    command = ["/bin/launchctl", verb, f"gui/{os.getuid()}/{label}"]

    class LaunchctlReply:
        """The manager accepts work; its client cannot see a reply until the delayed spawn."""

        status = None

        def poll(self):
            return self.status

        def wait(self, *, timeout):
            self.status = self.status if self.status is not None else 0
            return self.status

        def kill(self):
            self.status = -9

    class ReplySelector:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def register(self, *_args):
            pass

        def select(self, timeout):
            nonlocal elapsed_now
            elapsed_now += min(timeout, elapsed)
            return [True] if timeout > elapsed else []

    child = LaunchctlReply()

    def spawn(argv, **_kwargs):
        assert argv == command
        return child

    with Path(os.devnull).open("rb") as child.stdout:
        monkeypatch.setattr(_service.subprocess, "Popen", spawn)
        monkeypatch.setattr(_service.selectors, "DefaultSelector", ReplySelector)
        monkeypatch.setattr(_service.time, "monotonic", lambda: elapsed_now)
        result = _service.bounded_manager_command(command, deadline=explicit_deadline)
    assert result.returncode == (0 if accepted else None)
    assert child.status == (0 if accepted else -9)


def test_archive_refuses_source_replaced_after_plan_recheck(interrupted, monkeypatch):
    recovery, _, _, marker, request, incident = interrupted
    plan = recovery.plan(incident)
    before_request = request.read_bytes()
    actual_move = recovery_module.rename_exclusive
    replacement = json.dumps({"token": "f" * 64}, indent=2) + "\n"

    def racing_move(source, destination, *, expected_source):
        private_write(source, replacement)
        actual_move(source, destination, expected_source=expected_source)

    monkeypatch.setattr(recovery_module, "rename_exclusive", racing_move)
    with pytest.raises(HeinzelError, match="changed immediately before"):
        recovery.resume(incident, plan["digest"])
    assert marker.read_text() == replacement
    assert request.read_bytes() == before_request
    assert not Path(plan["archive"]).exists()


@pytest.mark.parametrize("interrupted", [True], indirect=True)
def test_manual_interruption_followed_by_automatic_refusal_has_no_incident_authority(interrupted):
    recovery, _, manager, marker, request, local_hashes = interrupted
    reviewed_for_other_mutation = ("a" * 64, local_hashes[1], local_hashes[2])
    before = marker.read_bytes(), request.read_bytes()
    with pytest.raises(HeinzelError):
        recovery.plan(reviewed_for_other_mutation)
    assert (marker.read_bytes(), request.read_bytes()) == before
    assert not manager.verbs()
