"""Real updater/pairing startup shares private state and migrates only updater-only legacy folders."""

import os
import stat

import pytest
from fastapi.testclient import TestClient

from heinzel import HeinzelError
from heinzel.heads import web
from heinzel.heads.web import _state_directory as storage
from heinzel.heads.web._auto_update import AutoUpdater
from heinzel.heads.web._devices import Devices

pytestmark = pytest.mark.integration
TOKEN = "shared-directory-session"
BASE = "http://127.0.0.1:8000"


@pytest.mark.parametrize("first", ["updater", "devices", "legacy-updater"])
def test_shared_startup_pairs_and_reopens_without_weakening_widget_storage(make_kernel, monkeypatch, first):
    kernel = make_kernel(builtins=True)
    directory = kernel.settings.state_dir / "web"
    devices = Devices(directory)
    if first == "devices":
        devices.pair("existing browser")
    elif first == "legacy-updater":
        directory.mkdir(parents=True, mode=0o755)
        directory.chmod(0o755)
        (directory / "auto-update.lock").write_bytes(b"legacy updater lock")
    monkeypatch.setattr(web, "devices_for_user", lambda: devices)
    client = TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url=BASE)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    paired = client.post("/api/_web/device", headers={"Authorization": f"Bearer {TOKEN}"}, json={"name": "phone"})
    assert paired.status_code == 200, paired.text
    credential = paired.json()
    headers = {"Authorization": "Bearer " + credential["token"]}
    reopened = TestClient(web.build(kernel, port=8000, token=TOKEN + "new", static=None), base_url=BASE)
    assert reopened.get("/api/system/notify/list", headers=headers).status_code == 200
    assert stat.S_IMODE(devices.path.stat().st_mode) == 0o600
    assert reopened.post("/api/_web/widget-credential", headers=headers).status_code == 200
    directory.chmod(0o755)
    with pytest.raises(HeinzelError):
        AutoUpdater(kernel, restart=None)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o755


@pytest.mark.parametrize("mode", [0o755, 0o750])
def test_legacy_migration_preserves_only_owned_updater_state(tmp_path, mode):
    directory = tmp_path / "web"
    directory.mkdir(mode=mode)
    directory.chmod(mode)
    for name in storage.UPDATER_FILES:
        (directory / name).write_bytes(b"existing updater state")
    with storage.private_directory(directory, migrate_updater=True) as folder:
        assert stat.S_IMODE(os.fstat(folder).st_mode) == 0o700
    assert all((directory / name).read_bytes() == b"existing updater state" for name in storage.UPDATER_FILES)


@pytest.mark.parametrize(
    "name",
    ["devices.json", "devices.lock", "widget-workspace", "widget-fenced-deadbeef", ".devices-partial", "unknown"],
)
def test_legacy_migration_never_repairs_or_resets_credential_state(tmp_path, name):
    directory = tmp_path / "web"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    target = directory / name
    target.write_bytes(b"preserve even damaged state")
    with pytest.raises(HeinzelError), storage.private_directory(directory, migrate_updater=True):
        pytest.fail("unsafe credential state was admitted")
    assert stat.S_IMODE(directory.stat().st_mode) == 0o755
    assert target.read_bytes() == b"preserve even damaged state"


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "writable", "foreign"])
def test_legacy_migration_rejects_unsafe_updater_entries(tmp_path, monkeypatch, kind):
    directory = tmp_path / "web"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    outside = tmp_path / "outside"
    outside.write_bytes(b"untouched")
    target = directory / "auto-update.json"
    if kind == "symlink":
        target.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, target)
    elif kind == "fifo":
        os.mkfifo(target, 0o600)
    else:
        target.write_bytes(b"untouched")
        target.chmod(0o666 if kind == "writable" else 0o644)
    if kind == "foreign":
        original = storage.os.fstat

        def foreign(descriptor):
            info = original(descriptor)
            values = list(info)
            if stat.S_ISREG(info.st_mode):
                values[4] += 1
            return os.stat_result(values)

        monkeypatch.setattr(storage.os, "fstat", foreign)
    with pytest.raises((HeinzelError, OSError)), storage.private_directory(directory, migrate_updater=True):
        pytest.fail("unsafe updater entry was admitted")
    assert stat.S_IMODE(directory.stat().st_mode) == 0o755
    assert outside.read_bytes() == b"untouched"


@pytest.mark.parametrize("kind", ["symlink", "parent-symlink", "writable", "foreign", "writable-parent"])
def test_legacy_migration_rejects_unsafe_directory(tmp_path, monkeypatch, kind):
    parent = tmp_path / "state"
    parent.mkdir(mode=0o700)
    directory = parent / "web"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    if kind == "symlink":
        moved = parent / "original"
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    elif kind == "parent-symlink":
        moved = tmp_path / "original"
        parent.rename(moved)
        parent.symlink_to(moved, target_is_directory=True)
    elif kind == "writable":
        directory.chmod(0o777)
    elif kind == "writable-parent":
        parent.chmod(0o777)
    else:
        owner = os.getuid()
        monkeypatch.setattr(storage.os, "getuid", lambda: owner + 1)
    before = directory.stat().st_mode
    with pytest.raises((HeinzelError, OSError)), storage.private_directory(directory, migrate_updater=True):
        pytest.fail("unsafe directory was admitted")
    assert directory.stat().st_mode == before
