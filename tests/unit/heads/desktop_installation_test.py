"""Exercise the owned desktop transaction against an isolated home."""

import json
import os
import platform
import plistlib
import shutil
import signal
import struct
import subprocess
import sys
from functools import partial
from pathlib import Path

import pytest

from heinzel.heads import _desktop_installation as desktop

pytestmark = pytest.mark.unit


def fetch(version, directory):
    archive = directory / "archive"
    archive.write_text(version)
    return archive


def extract(archive, staging):
    bundle = staging / desktop.BUNDLE_NAME
    bundle.mkdir()
    version = archive.read_text()
    (bundle / "release").write_text(version)
    contents = bundle / "Contents"
    macos = contents / "MacOS"
    macos.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "org.heinzelmaennchen.desktop",
                "CFBundleExecutable": "Heinzelmaennchen",
                "CFBundleShortVersionString": version,
            }
        )
    )
    app = macos / "Heinzelmaennchen"
    app.write_bytes(struct.pack("<8I", 0xFEEDFACF, 0x0100000C, 0, 2, 0, 0, 0, 0))
    app.chmod(0o700)
    return bundle


@pytest.fixture
def local_installation(monkeypatch, tmp_path):
    executable = tmp_path / "bin" / "hz"
    executable.parent.mkdir()
    executable.touch(mode=0o700)

    def prepare(bundle, *, keep_quarantine):
        (bundle / ("quarantined" if keep_quarantine else "sealed")).touch()

    def rename(source, target, *, swap=False):
        if not swap:
            if os.path.lexists(target):
                raise FileExistsError(target)
            source.rename(target)
            return
        holding = source.with_name("swap")
        source.rename(holding)
        target.rename(source)
        holding.rename(target)

    monkeypatch.setattr(desktop, "_check_platform", lambda: None)
    # Never the real /Applications: these cases cover the per-user fallback; desktop_location_test the system one.
    monkeypatch.setattr(desktop, "SYSTEM_APPLICATIONS", tmp_path / "no-system-Applications")
    if sys.platform != "darwin":
        monkeypatch.setattr(desktop, "_rename", rename)
    monkeypatch.setattr(desktop, "fetch_archive", fetch)
    monkeypatch.setattr(desktop, "extract_bundle", extract)
    monkeypatch.setattr(desktop, "_prepare_for_gatekeeper", prepare)
    monkeypatch.setattr(desktop, "_in_use", lambda _: False)

    return executable, tmp_path / "Applications" / desktop.BUNDLE_NAME, tmp_path / ".config" / "heinzel"


def test_install_update_and_remove_preserve_profile(local_installation, tmp_path):
    executable, bundle, config = local_installation
    assert desktop.install_desktop("1.0.0", executable, update_only=True, home=tmp_path) == "absent"
    assert not bundle.parent.exists()
    assert desktop.install_desktop("1.0.0", executable, home=tmp_path) == "installed"
    assert (bundle / "sealed").is_file()
    assert not (bundle / "quarantined").exists()
    assert desktop.install_desktop("1.0.0", executable, home=tmp_path) == "current"
    assert json.loads((config / desktop._BINDING).read_text()) == {"version": 1, "executable": str(executable)}
    assert desktop.install_desktop("1.0.1", executable, update_only=True, home=tmp_path) == "updated"
    assert desktop.install_desktop("1.0.0", executable, update_only=True, home=tmp_path) == "current"
    assert (bundle / "release").read_text() == "1.0.1"
    assert (bundle / "sealed").is_file()
    (config / "profile.json").write_text("{}")
    assert desktop.uninstall_desktop(home=tmp_path)
    assert not bundle.exists()
    assert not (config / desktop._BINDING).exists()
    assert not (config / desktop._RECEIPT).exists()
    assert (config / "profile.json").read_text() == "{}"
    assert not desktop.uninstall_desktop(home=tmp_path)


@pytest.mark.parametrize("kind", ["foreign", "symlink", "replaced", "binding"])
def test_ownership_conflicts_fail_closed(kind, local_installation, tmp_path):
    executable, bundle, config = local_installation
    if kind in {"replaced", "binding"}:
        desktop.install_desktop("1.0.0", executable, home=tmp_path)
        if kind == "replaced":
            bundle.rename(bundle.with_name("old.app"))
            bundle.mkdir()
        else:
            (config / desktop._BINDING).write_text("{}")
    else:
        bundle.parent.mkdir()
        if kind == "symlink":
            bundle.symlink_to(tmp_path / "missing")
        else:
            bundle.mkdir()

    with pytest.raises(desktop.DesktopInstallationError, match="ownership receipt"):
        desktop.install_desktop("1.0.1", executable, home=tmp_path)
    with pytest.raises(desktop.DesktopInstallationError, match="ownership receipt"):
        desktop.uninstall_desktop(home=tmp_path)
    assert os.path.lexists(bundle)


def test_update_rechecks_running_app_after_staging(local_installation, monkeypatch, tmp_path):
    executable, bundle, config = local_installation
    desktop.install_desktop("1.0.0", executable, home=tmp_path)
    receipt = (config / desktop._RECEIPT).read_bytes()
    replacement_cli = executable.with_name("hz-new")
    replacement_cli.touch(mode=0o700)
    answers = iter([False, True, True])
    monkeypatch.setattr(desktop, "_in_use", lambda _: next(answers))
    assert desktop.install_desktop("1.0.1", replacement_cli, update_only=True, home=tmp_path) == "deferred"
    with pytest.raises(desktop.DesktopInstallationError, match="Quit Heinzelmännchen"):
        desktop.uninstall_desktop(home=tmp_path)
    assert (bundle / "release").read_text() == "1.0.0"
    assert (config / desktop._RECEIPT).read_bytes() == receipt
    assert json.loads((config / desktop._BINDING).read_text())["executable"] == str(executable)
    assert not list(bundle.parent.glob(".heinzel-desktop-*"))
    assert not list(config.glob(".heinzel-desktop-*"))


@pytest.mark.parametrize("entry", ["version", "identifier", "executable", "mode", "architecture"])
def test_mismatched_bundle_identity_is_rejected(entry, local_installation, monkeypatch, tmp_path):
    executable, bundle, _config = local_installation
    extract = desktop.extract_bundle

    def extract_wrong_bundle(archive, staging):
        candidate = extract(archive, staging)
        contents = candidate / "Contents"
        app = contents / "MacOS" / "Heinzelmaennchen"
        if entry == "mode":
            app.chmod(0o600)
        elif entry == "architecture":
            app.write_bytes(struct.pack("<8I", 0xFEEDFACF, 0x01000007, 0, 2, 0, 0, 0, 0))
        else:
            keys = {
                "version": "CFBundleShortVersionString",
                "identifier": "CFBundleIdentifier",
                "executable": "CFBundleExecutable",
            }
            plist = contents / "Info.plist"
            identity = plistlib.loads(plist.read_bytes())
            identity[keys[entry]] = "different"
            plist.write_bytes(plistlib.dumps(identity))
        return candidate

    monkeypatch.setattr(desktop, "extract_bundle", extract_wrong_bundle)
    with pytest.raises(desktop.DesktopInstallationError, match="expected Apple silicon"):
        desktop.install_desktop("1.0.0", executable, home=tmp_path)
    assert not bundle.exists()


@pytest.mark.parametrize("operation", ["install", "update", "uninstall"])
def test_receipt_publication_failure_rolls_back(operation, local_installation, monkeypatch, tmp_path):
    executable, bundle, config = local_installation
    if operation != "install":
        desktop.install_desktop("1.0.0", executable, home=tmp_path)
        original_inode = bundle.stat().st_ino
    rename = desktop._rename

    def fail_receipt(source, target, *, swap=False):
        if source.name == desktop._RECEIPT:
            msg = "simulated write failure"
            raise OSError(msg)
        rename(source, target, swap=swap)

    with monkeypatch.context() as patch:
        patch.setattr(desktop, "_rename", fail_receipt)
        mutate = (
            partial(desktop.uninstall_desktop, home=tmp_path)
            if operation == "uninstall"
            else partial(desktop.install_desktop, "1.0.1", executable, home=tmp_path)
        )
        with pytest.raises(OSError, match="simulated write failure"):
            mutate()
    if operation == "install":
        assert not bundle.exists()
        assert not (config / desktop._BINDING).exists()
    else:
        assert (bundle / "release").read_text() == "1.0.0"
        assert bundle.stat().st_ino == original_inode
        assert desktop.install_desktop("1.0.0", executable, home=tmp_path) == "current"


_CRASH_SCRIPT = """
import os
import runpy
import signal
import sys
from pathlib import Path
from heinzel.heads import _desktop_installation as desktop

test_file, operation, boundary, executable, home = sys.argv[1:]
helpers = runpy.run_path(test_file)
desktop.fetch_archive = helpers["fetch"]
desktop.extract_bundle = helpers["extract"]
desktop._prepare_for_gatekeeper = lambda _bundle, **_options: None
desktop.SYSTEM_APPLICATIONS = Path(home) / "no-system-Applications"
desktop._in_use = lambda _: False
rename = desktop._rename
write_journal = desktop._write_journal
count = 0

def crash_after_rename(*args, **kwargs):
    global count
    rename(*args, **kwargs)
    count += 1
    if str(count) == boundary:
        os.kill(os.getpid(), signal.SIGKILL)

def crash_after_commit(config, journal):
    write_journal(config, journal)
    if journal.state == "committed" and boundary == "commit":
        os.kill(os.getpid(), signal.SIGKILL)

desktop._rename = crash_after_rename
desktop._write_journal = crash_after_commit
if operation == "uninstall":
    desktop.uninstall_desktop(home=Path(home))
else:
    desktop.install_desktop("1.0.1", Path(executable), home=Path(home))
"""


@pytest.mark.skipif(sys.platform != "darwin" or platform.machine() != "arm64", reason="requires Apple silicon macOS")
@pytest.mark.parametrize("boundary", ["1", "2", "3", "commit"])
@pytest.mark.parametrize("operation", ["install", "update", "uninstall"])
def test_sigkill_leaves_recoverable_transaction(operation, boundary, local_installation, tmp_path):
    executable, bundle, config = local_installation
    if operation != "install":
        desktop.install_desktop("1.0.0", executable, home=tmp_path)
        original_inode = bundle.stat().st_ino

    process = subprocess.run(
        [sys.executable, "-c", _CRASH_SCRIPT, __file__, operation, boundary, str(executable), str(tmp_path)],
        check=False,
        timeout=5,
    )
    assert process.returncode == -signal.SIGKILL
    assert (config / desktop._JOURNAL).is_file()

    if boundary == "commit":
        if operation == "uninstall":
            assert not desktop.uninstall_desktop(home=tmp_path)
        else:
            assert desktop.install_desktop("1.0.1", executable, update_only=True, home=tmp_path) == "current"
            assert (bundle / "release").read_text() == "1.0.1"
    elif operation == "install":
        assert desktop.install_desktop("1.0.1", executable, update_only=True, home=tmp_path) == "absent"
        assert not (config / desktop._BINDING).exists()
    else:
        assert desktop.install_desktop("1.0.0", executable, update_only=True, home=tmp_path) == "current"
        assert bundle.stat().st_ino == original_inode
        assert (bundle / "release").read_text() == "1.0.0"
    assert not (config / desktop._JOURNAL).exists()
    assert not list(config.glob(".heinzel-desktop-*"))
    assert not list(bundle.parent.glob(".heinzel-desktop-*"))


@pytest.mark.skipif(sys.platform != "darwin" or platform.machine() != "arm64", reason="requires Apple silicon macOS")
def test_recovery_leaves_replaced_foreign_bundle_untouched(local_installation, tmp_path):
    executable, bundle, config = local_installation
    desktop.install_desktop("1.0.0", executable, home=tmp_path)
    process = subprocess.run(
        [sys.executable, "-c", _CRASH_SCRIPT, __file__, "update", "1", str(executable), str(tmp_path)],
        check=False,
        timeout=5,
    )
    assert process.returncode == -signal.SIGKILL
    binding = (config / desktop._BINDING).stat().st_ino
    bundle.rename(bundle.with_name("old.app"))
    bundle.mkdir()
    (bundle / "foreign").touch()
    with pytest.raises(desktop.DesktopInstallationError, match="ownership receipt"):
        desktop.install_desktop("1.0.0", executable, update_only=True, home=tmp_path)
    assert (bundle / "foreign").is_file()
    assert (config / desktop._BINDING).stat().st_ino == binding
    assert (config / desktop._JOURNAL).is_file()


@pytest.mark.skipif(sys.platform != "darwin" or platform.machine() != "arm64", reason="requires Apple silicon macOS")
def test_lockf_launch_lease_defers_update_and_uninstall(local_installation, tmp_path):
    executable, bundle, config = local_installation
    assert desktop.install_desktop("1.0.0", executable, home=tmp_path) == "installed"
    lease = config / desktop._USE_LOCK
    assert lease.stat().st_mode & 0o777 == 0o600
    with (
        lease.open("rb") as file,
        subprocess.Popen(
            ["/usr/bin/lockf", "-t", "0", f"/dev/fd/{file.fileno()}", "/bin/sh", "-c", "echo ready; exec /bin/cat"],
            pass_fds=(file.fileno(),),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        ) as launcher,
    ):
        assert launcher.stdout is not None
        try:
            assert launcher.stdout.readline() == b"ready\n"
            assert desktop.install_desktop("1.0.1", executable, update_only=True, home=tmp_path) == "deferred"
            with pytest.raises(desktop.DesktopInstallationError, match="Quit Heinzelmännchen"):
                desktop.uninstall_desktop(home=tmp_path)
            assert (bundle / "release").read_text() == "1.0.0"
        finally:
            launcher.communicate(timeout=5)
    assert desktop.install_desktop("1.0.1", executable, update_only=True, home=tmp_path) == "updated"


@pytest.mark.skipif(sys.platform != "darwin" or platform.machine() != "arm64", reason="requires Apple silicon macOS")
def test_native_quarantine_and_process_detection(tmp_path):
    bundle = tmp_path / desktop.BUNDLE_NAME
    bundle.mkdir()
    desktop.mark_quarantined(bundle)
    quarantine = subprocess.check_output(["/usr/bin/xattr", "-p", "com.apple.quarantine", str(bundle)], timeout=5)
    assert quarantine.startswith(b"0081;")
    sleep = Path(shutil.copyfile("/bin/sleep", bundle / "sleep"))
    sleep.chmod(0o700)
    with subprocess.Popen([str(sleep), "20"]) as process:
        try:
            assert desktop._in_use(bundle)
        finally:
            process.terminate()
    assert not desktop._in_use(bundle)
