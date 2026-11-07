"""A mutation marker whose owner provably ended is archived with a receipt; every uncertain case keeps it pending."""

import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from heinzel import HeinzelError
from heinzel.modules.system.daemon import DaemonService, DaemonSettings
from heinzel.modules.system.daemon._abandoned_mutation import host_boot, marker_token_matches, parse_marker
from heinzel.modules.system.daemon._runtime import lock
from tests.fixtures.daemon_manager import RecordedManager as Runner
from tests.fixtures.daemon_manager import record_tool

pytestmark = pytest.mark.unit

BOOT_BEFORE = "11111111-1111-4111-8111-111111111111"
BOOT_AFTER = "22222222-2222-4222-8222-222222222222"
TOKEN = "a" * 64


class Host:
    """The boot the service sees: its identity and wall-clock start."""

    def __init__(self) -> None:
        self.boot: str | None = BOOT_BEFORE
        self.started: int | None = 1

    def __call__(self) -> tuple[str | None, int | None]:
        return self.boot, self.started

    def reboot(self, marker: Path) -> None:
        self.boot = BOOT_AFTER
        self.started = marker.stat().st_mtime_ns + 1_000_000_000


@pytest.fixture
def host() -> Host:
    return Host()


@pytest.fixture
def service(make_kernel, settings, tmp_path, host) -> DaemonService:
    binary = tmp_path / "bin"
    binary.mkdir()
    installation = record_tool(tmp_path / "tool-store/heinzel", binary)
    runner = Runner(binary)
    built = DaemonService(
        make_kernel(),
        settings,
        DaemonSettings(unit_dir=tmp_path / "units"),
        runner=runner,
        platform="darwin",
        uid=lambda: 501,
        clock=lambda: datetime(2026, 11, 7, 8, 0, tzinfo=UTC),
        which=lambda name: f"/bin/{name}",
        desktop_session=lambda: "Aqua",
        birth_fence=lambda: None,
        process_birth=lambda: None,
        quiescence_installation=installation,
        quiescence_probe=runner.probe,
        linux_manager_identity=lambda: (1, 2, 3),
        continuity_fingerprint=installation.startup_fingerprint,
        host_boot_evidence=host,
    )
    runner.attach(built)
    return built


def _marker(service: DaemonService) -> Path:
    return service.state_dir / "mutation-pending.json"


def _legacy(service: DaemonService) -> Path:
    """The token-only marker every released version wrote, as an auto-update that died left it."""
    service._write_state("mutation-pending.json", {"token": TOKEN})
    return _marker(service)


def _receipts(service: DaemonService) -> list[dict]:
    paths = sorted(service.state_dir.glob("abandoned-mutation-*.receipt.json"))
    return [json.loads(path.read_text()) for path in paths]


def _dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=10)
    return child.pid


def test_reboot_recovers_a_legacy_token_only_marker_at_the_next_tick(service, host):
    marker = _legacy(service)
    original = marker.read_bytes()
    assert service.tick().locked  # same boot: nothing proves the writer is gone
    assert marker.exists()
    host.reboot(marker)
    report = service.tick()
    assert not report.locked
    assert not marker.exists()
    (receipt,) = _receipts(service)
    assert receipt["evidence"] == "previous-boot"
    assert receipt["status"] == "archived"
    assert receipt["marker_form"] == "legacy-token"
    assert receipt["current_boot"] == BOOT_AFTER
    archive = Path(receipt["archive"])
    assert archive.parent == service.state_dir
    assert archive.read_bytes() == original  # archived with its exact bytes, never deleted
    assert receipt["verified_generation"] == service._quiescence_installation.startup_fingerprint()


def test_update_guard_recovers_a_marker_from_an_earlier_boot_and_proceeds(service, host):
    with pytest.raises(RuntimeError), service.mutation_guard(lambda: True, lambda: "b" * 64):
        raise RuntimeError  # the update died before verifying anything: its marker stays
    recorded = parse_marker(_marker(service).read_bytes())
    assert recorded is not None
    assert (recorded.pid, recorded.boot) == (os.getpid(), BOOT_BEFORE)
    with pytest.raises(HeinzelError, match="requires recovery"), service.mutation_guard(lambda: True, lambda: "b" * 64):
        pytest.fail("the owner is this live process in this boot")
    host.boot = BOOT_AFTER
    host.started = None  # the recorded boot identity alone proves the earlier boot
    with service.mutation_guard(lambda: True, lambda: "b" * 64) as scope:
        scope.capture()
        scope.abort_unchanged()
    assert not _marker(service).exists()
    assert [receipt["evidence"] for receipt in _receipts(service)] == ["previous-boot"]


def test_restart_recovers_a_same_boot_marker_whose_process_exited(service):
    service._write_state("mutation-pending.json", {"token": TOKEN, "pid": _dead_pid(), "boot": BOOT_BEFORE})
    result = service.restart()
    assert not result.restarted  # nothing installed; the point is that it no longer refuses
    assert not _marker(service).exists()
    assert [receipt["evidence"] for receipt in _receipts(service)] == ["owner-exited"]


def test_a_live_owner_in_the_same_boot_keeps_the_marker(service):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        service._write_state("mutation-pending.json", {"token": TOKEN, "pid": child.pid, "boot": BOOT_BEFORE})
        assert service.tick().locked
        with pytest.raises(HeinzelError, match="requires recovery"):
            service.restart()
    finally:
        child.kill()
        child.wait(timeout=10)
    assert _marker(service).exists()
    assert not _receipts(service)


@pytest.mark.parametrize(
    ("boot", "started"),
    [
        (None, None),  # nothing about the boot is known
        (BOOT_AFTER, 1),  # identities say earlier boot, the clock says this one: contradictory
    ],
)
def test_unknown_or_contradictory_boot_evidence_keeps_the_marker(service, host, boot, started):
    service._write_state("mutation-pending.json", {"token": TOKEN, "pid": os.getpid(), "boot": BOOT_BEFORE})
    host.boot, host.started = boot, started
    assert service.tick().locked
    assert _marker(service).exists()


def test_a_held_lifecycle_lock_keeps_the_marker_even_after_reboot(service, host):
    marker = _legacy(service)
    host.reboot(marker)
    with lock(service.state_dir / "lifecycle.lock") as acquired:
        assert acquired
        with pytest.raises(HeinzelError, match="requires recovery"):
            service.restart()
    assert marker.exists()


def test_an_older_handover_request_from_a_completed_handover_does_not_block(service, host):
    service._write_state("handover-request.json", {"request_id": "d" * 64})
    earlier = (service.state_dir / "handover-request.json").stat().st_mtime_ns - 1_000_000_000
    os.utime(service.state_dir / "handover-request.json", ns=(earlier, earlier))
    marker = _legacy(service)
    host.reboot(marker)
    assert not service.tick().locked
    assert not marker.exists()


def test_a_marker_replaced_before_the_move_stays_and_no_error_escapes(service, host, monkeypatch):
    from heinzel.modules.system.daemon import _abandoned_mutation

    marker = _legacy(service)
    host.reboot(marker)

    def changed(*_args, **_kwargs):
        msg = "The file changed immediately before the recovery move."
        raise HeinzelError(msg)

    monkeypatch.setattr(_abandoned_mutation, "rename_exclusive", changed)
    assert service.tick().locked
    assert marker.exists()
    assert [receipt["status"] for receipt in _receipts(service)] == ["archiving"]


def test_an_unverified_installation_keeps_the_marker(service, host):
    marker = _legacy(service)
    host.reboot(marker)
    (service._quiescence_installation.prefix / "bin/hz").write_text("half-written by a dead uv")
    assert service.tick().locked
    assert marker.exists()
    assert not _receipts(service)


@pytest.mark.parametrize("gate", ["before-update", "standard-install", "handover-pending", "newer-request"])
def test_another_gate_from_the_attempt_keeps_the_marker(service, host, gate):
    marker = _legacy(service)
    state = service.state_dir.parent
    if gate == "before-update":
        (state / "auto-update/before-update").mkdir(parents=True)
    elif gate == "standard-install":
        service._write_state("standard-install-pending.json", {"token": "c" * 64})
    elif gate == "handover-pending":
        service._write_state("handover-pending.json", {"request_id": "d" * 64})
    else:
        service._write_state("handover-request.json", {"request_id": "d" * 64})
        later = marker.stat().st_mtime_ns + 1_000_000
        os.utime(service.state_dir / "handover-request.json", ns=(later, later))
    host.reboot(marker)
    assert service.tick().locked
    assert marker.exists()


@pytest.mark.parametrize(
    "content",
    [
        b"uncertain",
        b'{"token": "' + b"a" * 64 + b'"}\n',  # not the indented form any version wrote
        (json.dumps({"token": TOKEN, "pid": 0}, indent=2) + "\n").encode(),
        (json.dumps({"token": TOKEN, "pid": 1, "boot": "not-a-uuid"}, indent=2) + "\n").encode(),
        (json.dumps({"token": TOKEN, "extra": 1}, indent=2) + "\n").encode(),
    ],
)
def test_an_unrecognized_marker_is_never_recovered(service, host, content):
    marker = _marker(service)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_bytes(content)
    marker.chmod(0o600)
    host.reboot(marker)
    assert service.tick().locked
    assert marker.read_bytes() == content


def test_marker_forms_match_only_their_own_token():
    legacy = (json.dumps({"token": TOKEN}, indent=2) + "\n").encode()
    owner = (json.dumps({"token": TOKEN, "pid": 7, "boot": BOOT_BEFORE}, indent=2) + "\n").encode()
    assert marker_token_matches(legacy, TOKEN)
    assert marker_token_matches(owner, TOKEN)
    assert not marker_token_matches(owner, "b" * 64)


def test_host_boot_reads_this_machine_without_a_subprocess(monkeypatch):
    def refuse(*_args, **_kwargs):
        pytest.fail("no process may start")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    identity, started = host_boot()
    if sys.platform in {"darwin", "linux"}:
        assert identity is not None
        assert uuid.UUID(identity)
        assert started is not None
        assert started < datetime.now(UTC).timestamp() * 1_000_000_000
        assert started > (datetime.now(UTC) - timedelta(days=3650)).timestamp() * 1_000_000_000
