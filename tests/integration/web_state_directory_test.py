"""Shared startup tightens owner-controlled state without trusting entries or following replacement paths."""

import os
import stat
import threading
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from heinzel import HeinzelError
from heinzel.heads import web
from heinzel.heads.web import _auto_update as auto
from heinzel.heads.web._auto_update import AutoUpdater
from heinzel.modules.system.client import _state_directory as storage
from heinzel.modules.system.client._devices import Devices

pytestmark = pytest.mark.integration
TOKEN = "shared-directory-session"
BASE = "http://127.0.0.1:8000"


@pytest.mark.parametrize("first", ["updater", "devices", "legacy-updater", "legacy-devices"])
def test_shared_startup_pairs_and_reopens_without_weakening_widget_storage(make_kernel, monkeypatch, first):
    kernel = make_kernel(builtins=True)
    directory = kernel.settings.state_dir / "web"
    devices = Devices(directory)
    existing = None
    if first in {"devices", "legacy-devices"}:
        _, existing = devices.pair("existing browser")
    elif first == "legacy-updater":
        directory.mkdir(parents=True, mode=0o755)
        (directory / "auto-update.lock").write_bytes(b"legacy updater lock")
    if first.startswith("legacy-"):
        directory.chmod(0o755)
    monkeypatch.setattr(web, "devices_for_user", lambda: devices)
    client = TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url=BASE)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    if existing:
        assert client.get("/api/system/notify/list", headers={"Authorization": "Bearer " + existing}).status_code == 200
    paired = client.post("/api/_web/device", headers={"Authorization": f"Bearer {TOKEN}"}, json={"name": "phone"})
    assert paired.status_code == 200, paired.text
    credential = paired.json()
    headers = {"Authorization": "Bearer " + credential["token"]}
    reopened = TestClient(web.build(kernel, port=8000, token=TOKEN + "new", static=None), base_url=BASE)
    assert reopened.get("/api/system/notify/list", headers=headers).status_code == 200
    assert stat.S_IMODE(devices.path.stat().st_mode) == 0o600
    assert reopened.post("/api/_web/widget-credential", headers=headers).status_code == 200
    updater = AutoUpdater(kernel, restart=None)
    updater.plan.message = "persisted updater state"
    updater._save()
    assert AutoUpdater(kernel, restart=None).plan.message == "persisted updater state"
    directory.chmod(0o777)
    degraded = TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url=BASE)
    assert degraded.get("/api/system/notify/list", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    refused = degraded.post("/api/_web/device", headers={"Authorization": f"Bearer {TOKEN}"}, json={"name": "new"})
    assert refused.status_code == 400
    assert stat.S_IMODE(directory.stat().st_mode) == 0o777


@pytest.mark.parametrize("mode", [0o700, 0o755, 0o750])
def test_directory_tightening_preserves_legacy_updater_bytes(tmp_path, mode):
    directory = tmp_path / "web"
    directory.mkdir(mode=mode)
    directory.chmod(mode)
    names = {"auto-update.json", "auto-update.lock", "auto-update-apply.lock"}
    for name in names:
        (directory / name).write_bytes(b"existing updater state")
    with storage.private_directory(directory) as folder:
        assert stat.S_IMODE(os.fstat(folder).st_mode) == 0o700
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert all((directory / name).read_bytes() == b"existing updater state" for name in names)


@pytest.mark.parametrize(
    "name",
    ["devices.json", "devices.lock", "widget-workspace", "widget-fenced-deadbeef", ".devices-partial", "unknown"],
)
def test_directory_tightening_never_repairs_or_resets_file_contents(tmp_path, name):
    directory = tmp_path / "web"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    target = directory / name
    target.write_bytes(b"preserve even damaged state")
    before = target.stat().st_mode
    with storage.private_directory(directory):
        pass
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert target.read_bytes() == b"preserve even damaged state"
    assert target.stat().st_mode == before


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "writable", "foreign"])
def test_updater_file_open_rejects_unsafe_entries(tmp_path, monkeypatch, kind):
    directory = tmp_path / "web"
    directory.mkdir(mode=0o700)
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
    with storage.private_directory(directory) as folder, pytest.raises((HeinzelError, OSError)):
        os.close(storage.open_updater_file(folder, "auto-update.json", os.O_RDONLY))
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert outside.read_bytes() == b"untouched"


@pytest.mark.parametrize(
    "kind", ["symlink", "parent-symlink", "writable", "foreign", "foreign-child", "writable-parent"]
)
def test_online_open_rejects_unsafe_directory(tmp_path, monkeypatch, kind):
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
    elif kind == "foreign-child":
        child_inode = directory.stat().st_ino
        original_fstat = storage.os.fstat

        def foreign_child(descriptor):
            info = original_fstat(descriptor)
            if info.st_ino == child_inode:
                values = list(info)
                values[4] += 1
                return os.stat_result(values)
            return info

        monkeypatch.setattr(storage.os, "fstat", foreign_child)
    else:
        owner = os.getuid()
        monkeypatch.setattr(storage.os, "getuid", lambda: owner + 1)
    before = directory.stat().st_mode
    with pytest.raises((HeinzelError, OSError)), storage.private_directory(directory):
        pytest.fail("unsafe directory was admitted")
    assert directory.stat().st_mode == before


@pytest.mark.parametrize("mode", [0o700, 0o755])
def test_private_or_legacy_directory_both_require_trusted_parent(tmp_path, mode):
    parent = tmp_path / "state"
    parent.mkdir()
    directory = parent / "web"
    directory.mkdir(mode=mode)
    directory.chmod(mode)
    parent.chmod(0o777)
    with pytest.raises(HeinzelError, match="parent"), storage.private_directory(directory):
        pytest.fail("untrusted parent admitted")
    assert stat.S_IMODE(directory.stat().st_mode) == mode


@pytest.mark.parametrize("kind", ["unavailable", "credentials", "symlink", "writable-parent"])
def test_web_build_degrades_updater_without_admitting_unsafe_pairing(make_kernel, monkeypatch, tmp_path, kind):
    kernel = make_kernel(builtins=True)
    directory = kernel.settings.state_dir / "web"
    directory.mkdir(parents=True, mode=0o700)
    devices = Devices(directory)
    monkeypatch.setattr(web, "devices_for_user", lambda: devices)
    if kind == "unavailable":
        original_open = storage.os.open

        def unavailable(path, flags, *args, **kwargs):
            if path == "web" and flags & os.O_DIRECTORY:
                msg = "synthetic unavailable state"
                raise PermissionError(msg)
            return original_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(storage.os, "open", unavailable)
    elif kind == "credentials":
        devices.pair("existing")
        directory.chmod(0o777)
    elif kind == "symlink":
        original = directory.with_name("preserved-web")
        directory.rename(original)
        directory.symlink_to(original, target_is_directory=True)
    else:
        directory.parent.chmod(0o777)
    application = web.build(kernel, port=8000, token=TOKEN, static=None)
    client = TestClient(application, base_url=BASE, raise_server_exceptions=False)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/api/system/notify/list", headers=headers).status_code == 200
    response = client.post("/api/_web/device", headers=headers, json={"name": "refused"})
    assert response.status_code == (500 if kind in {"unavailable", "symlink"} else 400)
    application.state.auto_update.plan.message = "memory only"
    application.state.auto_update._save()
    assert not (directory / "auto-update.json").exists()
    assert not (directory / "auto-update.lock").exists()


@pytest.mark.parametrize("operation", ["save", "read", "apply-lock", "pair"])
def test_concurrent_path_replacement_cannot_redirect_verified_descriptor(make_kernel, monkeypatch, tmp_path, operation):
    kernel = make_kernel(builtins=True)
    updater = AutoUpdater(kernel, restart=None)
    directory = kernel.settings.state_dir / "web"
    updater.plan.message = "trusted original"
    updater._save()
    before = (directory / "auto-update.json").read_bytes()
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (outside / "auto-update.json").write_bytes(b"outside must remain untouched")
    moved = directory.with_name("original-web")
    ready, replaced = threading.Event(), threading.Event()

    def replace_directory():
        if ready.wait(5):
            directory.rename(moved)
            directory.symlink_to(outside, target_is_directory=True)
            replaced.set()

    original_directory = storage.private_directory

    @contextmanager
    def replace_after_verification(*args, **kwargs):
        with original_directory(*args, **kwargs) as folder:
            ready.set()
            assert replaced.wait(5), "replacement thread did not complete"
            yield folder

    monkeypatch.setattr(auto, "private_directory", replace_after_verification)
    from heinzel.modules.system.client import _devices

    monkeypatch.setattr(_devices, "private_directory", replace_after_verification)
    worker = threading.Thread(target=replace_directory)
    worker.start()
    try:
        if operation == "save":
            with updater._editing() as plan:
                plan.message = "saved through original descriptor"
            assert (moved / "auto-update.json").read_bytes() != before
        elif operation == "read":
            assert updater._read().message == "trusted original"
        elif operation == "apply-lock":
            handle = auto._try_lock(updater._apply_lock)
            assert handle is not None
            auto._unlock(handle)
            assert (moved / "auto-update-apply.lock").is_file()
        else:
            device, _ = Devices(directory).pair("safe original")
            assert device.id in (moved / "devices.json").read_text()
    finally:
        ready.set()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert (outside / "auto-update.json").read_bytes() == b"outside must remain untouched"
    assert {path.name for path in outside.iterdir()} == {"auto-update.json"}


def test_updater_save_never_removes_a_colliding_file_it_did_not_create(make_kernel, monkeypatch):
    kernel = make_kernel(builtins=True)
    updater = AutoUpdater(kernel, restart=None)
    directory = kernel.settings.state_dir / "web"
    collision = directory / ".auto-update.json.collision"
    collision.write_bytes(b"preserve existing collision")
    monkeypatch.setattr(auto.secrets, "token_hex", lambda _: "collision")
    updater.plan.message = "attempted save"
    updater._save()
    assert collision.read_bytes() == b"preserve existing collision"


@pytest.mark.parametrize("name", ["auto-update.json", "auto-update.lock", "auto-update-apply.lock"])
def test_private_directory_updater_files_do_not_follow_links(make_kernel, tmp_path, name):
    kernel = make_kernel(builtins=True)
    directory = kernel.settings.state_dir / "web"
    directory.mkdir(parents=True, mode=0o700)
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside must remain untouched")
    (directory / name).symlink_to(outside)
    updater = AutoUpdater(kernel, restart=None)
    updater.plan.message = "must not overwrite a linked entry"
    updater._save()
    if name == "auto-update-apply.lock":
        assert auto._try_lock(updater._apply_lock) is None
    assert (directory / name).is_symlink()
    assert outside.read_bytes() == b"outside must remain untouched"


@pytest.mark.parametrize("entry", ["devices.json", "widget-workspace", "unknown"])
def test_concurrent_owner_entry_insertion_does_not_bypass_file_validation(make_kernel, monkeypatch, entry):
    """Owner-controlled entries added at chmod are still independently validated by their consumer."""
    kernel = make_kernel(builtins=True)
    directory = kernel.settings.state_dir / "web"
    devices = Devices(directory)
    device, _ = devices.pair("existing")
    directory.chmod(0o755)
    (directory / "auto-update.lock").write_bytes(b"legacy lock")
    ready, inserted = threading.Event(), threading.Event()
    attempts = []
    original_chmod = os.fchmod

    def insert_entry():
        if ready.wait(5):
            (directory / entry).write_bytes(b"concurrent legacy state")
            inserted.set()

    def insert_before_chmod(descriptor, mode):
        attempts.append(mode)
        ready.set()
        assert inserted.wait(5), "legacy writer did not complete"
        return original_chmod(descriptor, mode)

    monkeypatch.setattr(storage.os, "fchmod", insert_before_chmod)
    worker = threading.Thread(target=insert_entry)
    worker.start()
    try:
        updater = AutoUpdater(kernel, restart=None)
        assert inserted.wait(5)
        updater.plan.message = "safe updater persistence"
        updater._save()
        if entry == "devices.json":
            with pytest.raises(HeinzelError):
                devices.pair("refused")
        elif entry == "widget-workspace":
            with pytest.raises(HeinzelError):
                devices.widget_issue(device.id, "owner")
        else:
            assert devices.pair("permitted")[0].name == "permitted"
    finally:
        ready.set()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert attempts == [0o700]
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert (directory / entry).read_bytes() == b"concurrent legacy state"
    assert (directory / "auto-update.lock").read_bytes() == b"legacy lock"
    assert AutoUpdater(kernel, restart=None).plan.message == "safe updater persistence"


@pytest.mark.parametrize("which", ["child", "parent"])
def test_directory_swap_before_chmod_tightens_only_the_opened_inode(tmp_path, monkeypatch, which):
    parent = tmp_path / "state"
    parent.mkdir(mode=0o700)
    directory = parent / "web"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    (directory / "original").write_bytes(b"preserved")
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o755)
    outside.chmod(0o755)
    outside_child = outside / "web"
    outside_child.mkdir(mode=0o755)
    outside_child.chmod(0o755)
    moved = tmp_path / "moved"
    ready, replaced = threading.Event(), threading.Event()
    original_chmod = os.fchmod

    def swap():
        if ready.wait(5):
            source = directory if which == "child" else parent
            source.rename(moved)
            source.symlink_to(outside, target_is_directory=True)
            replaced.set()

    def swap_before_chmod(descriptor, mode):
        ready.set()
        assert replaced.wait(5), "replacement thread did not complete"
        return original_chmod(descriptor, mode)

    monkeypatch.setattr(storage.os, "fchmod", swap_before_chmod)
    worker = threading.Thread(target=swap)
    worker.start()
    try:
        with storage.private_directory(directory) as folder:
            original = moved if which == "child" else moved / "web"
            assert os.fstat(folder).st_ino == original.stat().st_ino
            assert stat.S_IMODE(os.fstat(folder).st_mode) == 0o700
            assert os.listdir(folder) == ["original"]  # noqa: PTH208 - prove descriptor-relative contents after rename
    finally:
        ready.set()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert stat.S_IMODE(outside.stat().st_mode) == 0o755
    assert stat.S_IMODE(outside_child.stat().st_mode) == 0o755
    assert (original / "original").read_bytes() == b"preserved"


@pytest.mark.parametrize("mode", [0o700, 0o755])
def test_device_credentials_remain_private_and_unchanged_after_directory_open(tmp_path, mode):
    devices = Devices(tmp_path / "web")
    device, token = devices.pair("existing")
    child, workspace, _ = devices.widget_issue(device.id, "owner")
    before = devices.path.read_bytes()
    devices.directory.chmod(mode)
    assert devices.check(token).id == device.id
    assert devices.widget_check(child, "owner", workspace) == device.id
    assert devices.path.read_bytes() == before
    assert stat.S_IMODE(devices.directory.stat().st_mode) == 0o700
    devices.path.chmod(0o644)
    devices.directory.chmod(mode)
    assert devices.check(token) is None
    with pytest.raises(HeinzelError):
        devices.widget_check(child, "owner", workspace)
    assert devices.path.read_bytes() == before
    assert stat.S_IMODE(devices.path.stat().st_mode) == 0o644
