"""`hz system notify recover`: archive and restart the legacy-overwrite shape; refuse every other store untouched."""

import hashlib
import json
import os
import stat
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from heinzel import NoticeKind, Status
from heinzel.modules.system.notify import NotifyService, NotifySettings
from heinzel.modules.system.notify import _store as store_module
from heinzel.modules.system.notify._native import Native
from heinzel.modules.system.notify._store import INTEGRITY_LEGACY, INTEGRITY_UNAVAILABLE

pytestmark = pytest.mark.unit

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 11, 7, 9, 0, tzinfo=BERLIN)

# The shape found on a long-running install: the durable marker from the first durable receipt, then a writer from
# before durable notices replaced the key-bearing state with a plain list holding one test notice.
LEGACY_NOTICE = {
    "id": "20261028T182947480017Z-52d5c3",
    "kind": "test",
    "title": "Notifications work",
    "body": "This is how hz tells you that a draft is ready or a run failed.",
    "route": "/notifications",
    "command": "hz system notify list",
    "key": "test:5f2f740c",
    "urgent": True,
    "at": "2026-10-28T18:29:47.480017Z",
    "due": "2026-10-28T18:29:47.480017Z",
    "popup": True,
    "held": None,
    "delivered_by": None,
    "delivered_at": None,
}


def _runner(command, timeout=None):
    return subprocess.CompletedProcess(command, 0, "", "")


@pytest.fixture
def service(settings) -> NotifyService:
    return NotifyService(
        settings,
        NotifySettings.model_validate({"native": False, "repeat_hours": 0}),
        {"working_hours": "09-18", "timezone": "Europe/Berlin"},
        clock=lambda: NOW,
        notifier=Native("notify-send", "/usr/bin/notify-send", detail="through notify-send", runner=_runner),
        ui_url=lambda route: None,
    )


def _private(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_bytes(data)
    path.chmod(mode)


def _legacy_store(service: NotifyService) -> Path:
    directory = service.store.directory
    _private(directory / "durable-notices", b"1\n")
    _private(directory / "notices.json", json.dumps([LEGACY_NOTICE], indent=1).encode() + b"\n")
    _private(directory / "notices.lock", b"")
    _private(directory / "looked.json", b'{"at": "2026-11-07T07:18:47.649125+00:00"}\n')
    _private(directory / "native.json", b'{"at": "2026-10-29T05:46:55.921732+00:00", "problem": null}\n')
    _private(directory / "strays-forgotten-at", b"2026-11-07T07:18:35.538784+00:00", mode=0o644)
    os.utime(directory / "durable-notices", (1_761_650_870, 1_761_650_870))
    return directory


def _snapshot(directory: Path) -> dict[str, tuple[bytes, int]] | None:
    if not directory.exists():
        return None
    return {
        path.name: (path.read_bytes() if path.is_file() and not path.is_symlink() else b"", path.lstat().st_mode)
        for path in sorted(directory.iterdir())
    }


def _durable(service: NotifyService, key: str) -> bool:
    return service.emit_once(NoticeKind.HEINZEL, "Check-in", "Due", route="/heinzel", command="hz", key=key)


def test_the_legacy_shape_is_down_with_a_recovery_fix_and_fails_closed_before_recovery(service):
    _legacy_store(service)
    check = service.health()[0]
    assert (check.name, check.status, check.detail) == ("notification store", Status.DOWN, INTEGRITY_LEGACY)
    assert "hz system notify recover --apply" in check.fix
    assert "coherent trusted backup" in check.fix
    assert "Do not delete" in check.fix
    assert not _durable(service, "slot")


def test_without_apply_recovery_only_reports_and_writes_nothing(service):
    directory = _legacy_store(service)
    before = _snapshot(directory)
    result = service.recover(apply=False)
    assert (result.state, result.notices, result.keys, result.receipt) == ("recoverable", 1, 1, None)
    assert _snapshot(directory) == before
    assert not (directory.parent / "notify-recovery").exists()


def test_apply_archives_the_originals_with_a_receipt_and_restarts_a_verified_store(service):
    directory = _legacy_store(service)
    before = _snapshot(directory)
    originals = {name: os.lstat(directory / name) for name in before}

    result = service.recover(apply=True)

    assert (result.state, result.notices, result.keys) == ("recovered", 1, 1)
    archive = Path(result.receipt)
    assert archive.parent == directory.parent / "notify-recovery"
    assert stat.S_IMODE(archive.stat().st_mode) == 0o700
    receipt = json.loads((archive / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["replay_keys_before"] is None
    assert receipt["replay_keys_after"]["count"] == 1
    assert {entry["name"] for entry in receipt["originals"]} == set(before)
    for entry in receipt["originals"]:
        copy = archive / entry["name"]
        data, _ = before[entry["name"]]
        assert copy.read_bytes() == data
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()
        assert stat.S_IMODE(copy.stat().st_mode) == stat.S_IMODE(originals[entry["name"]].st_mode)
        assert entry["mode"] == f"{stat.S_IMODE(originals[entry['name']].st_mode):04o}"
        assert copy.stat().st_mtime_ns == originals[entry["name"]].st_mtime_ns
    new_state = service.store.path.read_bytes()
    assert hashlib.sha256(new_state).hexdigest() == receipt["new_state_sha256"]
    assert (directory / "durable-notices").read_bytes() == b"1\n"

    assert service.store.integrity_problem() is None
    assert all(check.status is not Status.DOWN for check in service.health())
    (old,) = service.recent()
    assert (old.id, old.popup, old.held) == (LEGACY_NOTICE["id"], False, "recovered")
    assert service.store.has_once(LEGACY_NOTICE["key"])
    # The kept notice's own key is a replay key now: recording it again adds nothing.
    assert _durable(service, LEGACY_NOTICE["key"])
    assert len(service.recent()) == 1
    assert _durable(service, "new-slot")
    assert len(service.recent()) == 2
    assert _durable(service, "new-slot")
    assert len(service.recent()) == 2


def test_recovery_is_idempotent_once_the_store_verifies(service):
    directory = _legacy_store(service)
    assert service.recover(apply=True).state == "recovered"
    after = _snapshot(directory)
    again = service.recover(apply=True)
    assert again.state == "healthy"
    assert _snapshot(directory) == after
    assert len(list((directory.parent / "notify-recovery").iterdir())) == 1


def test_a_valid_durable_store_and_a_missing_store_are_healthy_and_untouched(service):
    assert service.recover(apply=True).state == "healthy"
    assert _snapshot(service.store.directory) is None
    assert _durable(service, "slot")
    before = _snapshot(service.store.directory)
    assert service.recover(apply=True).state == "healthy"
    assert _snapshot(service.store.directory) == before
    assert not (service.store.directory.parent / "notify-recovery").exists()


def _key_bearing_damage(directory: Path) -> None:
    _private(directory / "notices.json", json.dumps({"notices": [], "durable_keys": 42}).encode())


def _missing_list(directory: Path) -> None:
    (directory / "notices.json").unlink()


def _unknown_marker(directory: Path) -> None:
    _private(directory / "durable-notices", b"2\n")


def _expiring_marker(directory: Path) -> None:
    _private(directory / "expiring-notices", b"1\n")


def _half_written(directory: Path) -> None:
    _private(directory / ".notices.json.123.abcd", b"[")


def _linked_list(directory: Path) -> None:
    target = directory.parent / "elsewhere.json"
    (directory / "notices.json").rename(target)
    (directory / "notices.json").symlink_to(target)


def _loose_list(directory: Path) -> None:
    (directory / "notices.json").chmod(0o644)


def _hard_linked_marker(directory: Path) -> None:
    os.link(directory / "durable-notices", directory.parent / "marker-copy")


def _invalid_notice(directory: Path) -> None:
    _private(directory / "notices.json", json.dumps([{"id": "x"}]).encode())


def _not_json(directory: Path) -> None:
    _private(directory / "notices.json", b"[{")


@pytest.mark.parametrize(
    "damage",
    [
        _key_bearing_damage,
        _missing_list,
        _unknown_marker,
        _expiring_marker,
        _half_written,
        _linked_list,
        _loose_list,
        _hard_linked_marker,
        _invalid_notice,
        _not_json,
    ],
)
def test_every_other_shape_is_refused_untouched(service, damage):
    directory = _legacy_store(service)
    damage(directory)
    before = _snapshot(directory)
    for apply in (False, True):
        result = service.recover(apply=apply)
        assert result.state == "refused"
        assert result.receipt is None
        assert "Notifications work" not in result.detail
    assert _snapshot(directory) == before
    assert not (directory.parent / "notify-recovery").exists()


def test_a_linked_store_directory_is_refused(service, tmp_path):
    real = tmp_path / "real-notify"
    real.mkdir(mode=0o700)
    service.store.directory.parent.mkdir(parents=True, exist_ok=True)
    service.store.directory.symlink_to(real)
    assert service.recover(apply=True).state == "refused"
    assert list(real.iterdir()) == []


def test_a_crash_before_the_state_replacement_leaves_the_store_as_it_was(service, monkeypatch):
    directory = _legacy_store(service)
    before = _snapshot(directory)
    real_write = store_module._write_private

    def crash(path, text):
        if path == service.store.path:
            msg = "power lost before the replacement"
            raise OSError(msg)
        real_write(path, text)

    monkeypatch.setattr(store_module, "_write_private", crash)
    result = service.recover(apply=True)
    assert result.state == "refused"
    assert "archived" in result.detail
    assert _snapshot(directory) == before
    assert service.store.integrity_problem() == INTEGRITY_LEGACY

    monkeypatch.setattr(store_module, "_write_private", real_write)
    assert service.recover(apply=True).state == "recovered"
    assert len(list((directory.parent / "notify-recovery").iterdir())) == 2
    assert service.store.integrity_problem() is None


def test_other_durable_damage_keeps_the_generic_backup_only_fix(service):
    directory = _legacy_store(service)
    _key_bearing_damage(directory)
    check = service.health()[0]
    assert (check.status, check.detail) == (Status.DOWN, INTEGRITY_UNAVAILABLE)
    assert "notify recover" not in check.fix
