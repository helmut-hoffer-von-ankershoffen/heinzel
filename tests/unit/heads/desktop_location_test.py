"""Where the desktop app goes: /Applications when this user may write there, else ~/Applications, and migration.

Every case uses a fake home and a fake system Applications folder; bundles are inert files and nothing is launched.
"""

import os
import plistlib
import shutil
import struct
import sys
from pathlib import Path

import pytest

from heinzel.heads import _desktop_installation as desktop

pytestmark = pytest.mark.unit


def _fetch(version: str, directory: Path, _reporter: object = None) -> Path:
    archive = directory / "archive"
    archive.write_text(version)
    return archive


def _extract(archive: Path, staging: Path) -> Path:
    version = archive.read_text()
    bundle = staging / desktop.BUNDLE_NAME
    macos = bundle / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (bundle / "release").write_text(version)
    (bundle / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "org.heinzelmaennchen.desktop",
                "CFBundleExecutable": "Heinzelmaennchen",
                "CFBundleShortVersionString": version,
            }
        )
    )
    executable = macos / "Heinzelmaennchen"
    executable.write_bytes(struct.pack("<8I", 0xFEEDFACF, 0x0100000C, 0, 2, 0, 0, 0, 0))
    executable.chmod(0o700)
    return bundle


def _rename(source: Path, target: Path, *, swap: bool = False) -> None:
    if not swap:
        if os.path.lexists(target):
            raise FileExistsError(target)
        source.rename(target)
        return
    holding = source.with_name("swap")
    source.rename(holding)
    target.rename(source)
    holding.rename(target)


@pytest.fixture
def mac(monkeypatch, tmp_path):
    """A fake home and a fake system Applications folder this user may write to, unless a case locks it."""
    home = tmp_path / "home"
    home.mkdir()
    system = tmp_path / "system" / "Applications"
    system.mkdir(parents=True, mode=0o755)
    executable = home / "hz"
    executable.touch(mode=0o700)
    monkeypatch.setattr(desktop, "SYSTEM_APPLICATIONS", system)
    monkeypatch.setattr(desktop, "_check_platform", lambda: None)
    monkeypatch.setattr(desktop, "fetch_archive", _fetch)
    monkeypatch.setattr(desktop, "extract_bundle", _extract)
    monkeypatch.setattr(desktop, "_prepare_for_gatekeeper", lambda _bundle, **_options: None)
    monkeypatch.setattr(desktop, "_in_use", lambda _bundle: False)
    if sys.platform != "darwin":
        monkeypatch.setattr(desktop, "_rename", _rename)
    yield home, system, executable
    system.chmod(0o755)


def _bundles(home: Path, system: Path) -> list[Path]:
    return [
        folder / desktop.BUNDLE_NAME
        for folder in (system, home / "Applications")
        if (folder / desktop.BUNDLE_NAME).exists()
    ]


@pytest.mark.parametrize("writable", [True, False], ids=["admin", "standard-user"])
def test_install_prefers_system_applications_and_falls_back_to_the_users_folder(mac, writable):
    home, system, executable = mac
    if not writable:
        system.chmod(0o555)
    assert desktop.install_desktop("1.0.0", executable, home=home) == "installed"
    expected = (system if writable else home / "Applications") / desktop.BUNDLE_NAME
    assert desktop.desktop_location(home) == expected
    assert _bundles(home, system) == [expected]
    assert not list(expected.parent.glob(".heinzel-desktop-*"))
    assert desktop.uninstall_desktop(home=home) == expected
    assert _bundles(home, system) == []
    assert desktop.desktop_location(home) is None


def test_a_world_writable_or_missing_system_folder_is_never_used(mac):
    _home, system, _executable = mac
    system.chmod(0o777)
    assert not desktop.system_applications_usable(system)
    assert not desktop.system_applications_usable(system.parent / "missing")
    system.chmod(0o755)
    assert desktop.system_applications_usable(system)


def test_reinstall_moves_an_older_user_folder_install_into_system_applications(mac):
    home, system, executable = mac
    system.chmod(0o555)
    desktop.install_desktop("1.0.0", executable, home=home)
    old = home / "Applications" / desktop.BUNDLE_NAME
    inode = old.stat().st_ino
    system.chmod(0o755)

    assert desktop.install_desktop("1.0.0", executable, home=home) == "current"
    moved = system / desktop.BUNDLE_NAME
    assert _bundles(home, system) == [moved]
    assert moved.stat().st_ino == inode
    assert desktop.desktop_location(home) == moved
    assert desktop.install_desktop("1.0.1", executable, home=home) == "updated"
    assert (moved / "release").read_text() == "1.0.1"
    assert _bundles(home, system) == [moved]
    assert desktop.uninstall_desktop(home=home) == moved
    assert _bundles(home, system) == []


def test_automatic_update_keeps_the_app_where_it_is(mac):
    home, system, executable = mac
    system.chmod(0o555)
    desktop.install_desktop("1.0.0", executable, home=home)
    system.chmod(0o755)
    assert desktop.install_desktop("1.0.1", executable, update_only=True, home=home) == "updated"
    assert _bundles(home, system) == [home / "Applications" / desktop.BUNDLE_NAME]


def test_a_running_app_is_not_moved(mac, monkeypatch):
    home, system, executable = mac
    system.chmod(0o555)
    desktop.install_desktop("1.0.0", executable, home=home)
    system.chmod(0o755)
    monkeypatch.setattr(desktop, "_in_use", lambda _bundle: True)
    assert desktop.install_desktop("1.0.0", executable, home=home) == "current"
    assert _bundles(home, system) == [home / "Applications" / desktop.BUNDLE_NAME]


def test_a_foreign_app_in_system_applications_is_never_replaced(mac):
    home, system, executable = mac
    foreign = system / desktop.BUNDLE_NAME
    foreign.mkdir()
    (foreign / "theirs").touch()
    with pytest.raises(desktop.DesktopInstallationError, match="not installed by hz app install"):
        desktop.install_desktop("1.0.0", executable, home=home)
    assert (foreign / "theirs").exists()

    foreign.rename(system / "elsewhere.app")
    system.chmod(0o555)
    desktop.install_desktop("1.0.0", executable, home=home)
    system.chmod(0o755)
    (system / "elsewhere.app").rename(foreign)
    assert desktop.install_desktop("1.0.0", executable, home=home) == "current"
    assert (foreign / "theirs").exists()
    assert desktop.desktop_location(home) == home / "Applications" / desktop.BUNDLE_NAME


def test_a_journal_for_an_unknown_folder_fails_closed(mac):
    home, _system, _executable = mac
    journal = desktop._Journal(
        operation="install",
        staging=".heinzel-desktop-abcdefgh",
        metadata=".heinzel-desktop-abcdefgh",
        staging_identity=(1, 1, None),
        metadata_identity=(1, 1, None),
        bundle=desktop._MoveIdentity(public=None, private=None),
        binding=desktop._MoveIdentity(public=None, private=None),
        receipt=desktop._MoveIdentity(public=None, private=None),
        applications="/tmp/elsewhere",  # noqa: S108 - a path the installer must refuse, never created
    )
    with pytest.raises(desktop.DesktopInstallationError, match="ownership receipt"):
        desktop._journal_bundle(home / "Applications" / desktop.BUNDLE_NAME, journal)


def test_an_unowned_app_in_system_applications_gets_a_clear_message(mac):
    home, system, executable = mac
    (system / desktop.BUNDLE_NAME).mkdir()
    with pytest.raises(desktop.DesktopInstallationError, match="not installed by hz app install"):
        desktop.install_desktop("1.0.0", executable, home=home)


@pytest.mark.parametrize(
    ("folder", "admin", "reason"),
    [
        ("system", True, "(for everyone on this Mac; you are an administrator)"),
        ("home", False, "(your Applications folder; this account is not an administrator, so {system} would need"),
        ("system", False, "(for everyone on this Mac)"),
    ],
)
def test_the_first_install_line_names_the_folder_and_the_true_reason(tmp_path, folder, admin, reason):
    from heinzel.heads import _desktop_messages as copy

    system = tmp_path / "system-applications"
    where = system if folder == "system" else tmp_path / "home" / "Applications"
    line = copy.installing("1.0.0", None, where, system, admin=admin)
    assert line.startswith(f"Installing Heinzelmännchen 1.0.0 into {where} (")
    assert reason.format(system=system) in line


def test_install_reports_one_row_per_step_group_when_not_a_terminal(mac):
    """Redirected or without a TTY: a header, then one line per row, no escape codes, never silent."""
    import io

    from rich.console import Console

    from heinzel.heads import _desktop_messages as copy
    from heinzel.heads.cli._progress import StepReporter

    home, system, executable = mac
    stream = io.StringIO()
    reporter = StepReporter(
        Console(file=stream, force_terminal=False, width=400),
        header="Installing",
        rows=copy.install_rows(keep_quarantine=False),
    )
    assert desktop.install_desktop("1.0.0", executable, home=home, reporter=reporter) == "installed"
    reporter.close(complete=True)
    text = stream.getvalue()
    assert "\x1b" not in text
    assert [line.split()[:2] for line in text.splitlines()] == [
        ["Installing"],
        ["ok", "Verified"],
        ["ok", "Sealed"],
        ["ok", "Installed"],
        ["ok", "Recorded"],
    ]
    assert f"ok Installed  {system / desktop.BUNDLE_NAME}" in text


def _record(home: Path) -> tuple[Path, Path]:
    config = home / ".config" / "heinzel"
    return config / desktop._RECEIPT, config / desktop._BINDING


def _orphan(mac) -> tuple[Path, Path, Path]:
    """Install, then remove the app by hand as Finder's Move to Trash does, leaving our record behind."""
    home, system, executable = mac
    assert desktop.install_desktop("1.0.0", executable, home=home) == "installed"
    shutil.rmtree(system / desktop.BUNDLE_NAME)
    return home, system, executable


def test_install_after_the_app_was_moved_to_the_trash_by_hand_asks_then_sets_the_old_record_aside(mac):
    home, system, executable = _orphan(mac)
    receipt, binding = _record(home)
    old = (receipt.read_bytes(), binding.read_bytes())
    with pytest.raises(desktop.LeftoverInstallError) as failure:
        desktop.install_desktop("1.0.0", executable, home=home)
    assert failure.value.problem is not None
    assert "the app is gone from" in failure.value.problem.happened
    assert str(receipt) in str(failure.value)
    assert receipt.read_bytes() == old[0]
    notes: list[str] = []
    assert desktop.install_desktop("1.0.0", executable, home=home, forget=True, notes=notes) == "installed"
    assert _bundles(home, system) == [system / desktop.BUNDLE_NAME]
    backups = sorted(receipt.parent.glob("*.orphaned-*"))
    assert [path.name.split(".orphaned-")[0] for path in backups] == [receipt.name, binding.name]
    assert {path.read_bytes() for path in backups} == set(old)
    assert notes == [f"Set aside  old install record ({backups[0]})"]
    assert desktop.desktop_location(home) == system / desktop.BUNDLE_NAME


def test_uninstall_after_the_app_was_moved_to_the_trash_by_hand_finishes(mac):
    home, _system, _executable = _orphan(mac)
    receipt, binding = _record(home)
    notes: list[str] = []
    assert desktop.uninstall_desktop(home=home, notes=notes) is None
    assert not receipt.exists()
    assert not binding.exists()
    assert len(list(receipt.parent.glob("*.orphaned-*"))) == 2
    assert len(notes) == 1
    assert desktop.uninstall_desktop(home=home) is None


def test_a_hand_copied_app_is_named_in_the_error_and_replace_moves_it_to_the_trash(mac):
    home, system, executable = mac
    copied = system / desktop.BUNDLE_NAME
    (copied / "Contents").mkdir(parents=True)
    (copied / "Contents" / "mine").write_text("keep me")
    (home / ".Trash").mkdir()
    receipt, _binding = _record(home)
    receipt.parent.mkdir(parents=True)
    receipt.parent.parent.chmod(0o700)
    receipt.parent.chmod(0o700)
    receipt.write_text("{}")
    receipt.chmod(0o600)
    with pytest.raises(desktop.DesktopInstallationError) as failure:
        desktop.install_desktop("1.0.0", executable, home=home)
    assert str(copied) in str(failure.value)
    assert failure.value.problem is not None
    assert "--replace" in failure.value.problem.action
    assert str(copied.parent) in failure.value.problem.happened
    assert (copied / "Contents" / "mine").read_text() == "keep me"

    notes: list[str] = []
    assert desktop.install_desktop("1.0.0", executable, home=home, replace=True, notes=notes) == "installed"
    (trashed,) = (home / ".Trash").iterdir()
    assert (trashed / "Contents" / "mine").read_text() == "keep me"
    assert (copied / "release").read_text() == "1.0.0"
    assert any("to the Trash" in note for note in notes)


def test_a_replaced_app_names_the_receipt_it_does_not_match(mac):
    home, system, executable = mac
    desktop.install_desktop("1.0.0", executable, home=home)
    bundle = system / desktop.BUNDLE_NAME
    bundle.rename(bundle.with_name("old.app"))  # keep the old one, so a freed inode number cannot be reused
    bundle.mkdir()
    receipt, _binding = _record(home)
    with pytest.raises(desktop.DesktopInstallationError) as failure:
        desktop.install_desktop("1.0.0", executable, home=home)
    assert f"{bundle} is not the copy recorded in {receipt}" in str(failure.value)


def test_replace_never_moves_the_app_this_installer_owns(mac):
    home, system, executable = mac
    (home / ".Trash").mkdir()
    desktop.install_desktop("1.0.0", executable, home=home)
    inode = (system / desktop.BUNDLE_NAME).stat().st_ino
    assert desktop.install_desktop("1.0.0", executable, home=home, replace=True) == "current"
    assert (system / desktop.BUNDLE_NAME).stat().st_ino == inode
    assert not list((home / ".Trash").iterdir())


def test_a_pending_journal_keeps_the_record_for_recovery(mac):
    home, _system, _executable = _orphan(mac)
    receipt, _binding = _record(home)
    (receipt.parent / desktop._JOURNAL).write_text("{}")
    assert desktop._clear_orphaned_record(home, receipt.parent, forget=True) == []
    assert receipt.exists()


def _events(caplog) -> list[tuple[str, str, object]]:
    return [
        (record.event, record.phase, getattr(record, "outcome", None))
        for record in caplog.records
        if record.name == "heinzel.desktop"
    ]


def test_install_and_uninstall_log_a_start_and_an_outcome_per_step(mac, caplog):
    home, system, executable = mac
    caplog.set_level("INFO", logger="heinzel.desktop")
    desktop.install_desktop("1.0.0", executable, home=home)
    assert _events(caplog) == [
        ("desktop.install.run", "start", None),
        ("desktop.install.recover", "start", None),
        ("desktop.install.recover", "end", "ok"),
        ("desktop.install.forget", "start", None),
        ("desktop.install.forget", "end", "ok"),
        ("desktop.install.locate", "start", None),
        ("desktop.install.locate", "end", "ok"),
        ("desktop.install.release", "start", None),
        ("desktop.install.release", "end", "installed"),
        ("desktop.install.run", "end", "installed"),
    ]
    release = next(r for r in caplog.records if r.event == "desktop.install.release" and r.phase == "end")
    assert release.path == str(system / desktop.BUNDLE_NAME)
    assert release.version == "1.0.0"
    assert isinstance(release.duration_ms, int)
    assert release.getMessage() == "desktop.install.release"

    caplog.clear()
    desktop.uninstall_desktop(home=home)
    assert ("desktop.uninstall.remove", "end", "ok") in _events(caplog)
    assert ("desktop.uninstall.run", "end", "removed") in _events(caplog)


def test_a_failed_step_logs_the_same_two_lines_the_person_reads(mac, caplog):
    home, _system, executable = _orphan(mac)
    caplog.set_level("INFO", logger="heinzel.desktop")
    with pytest.raises(desktop.LeftoverInstallError) as failure:
        desktop.install_desktop("1.0.0", executable, home=home)
    assert _events(caplog)[-2:] == [
        ("desktop.install.forget", "end", "failed"),
        ("desktop.install.run", "end", "failed"),
    ]
    failed = [r for r in caplog.records if r.phase == "end"][-1]
    assert failure.value.problem is not None
    assert (failed.happened, failed.action) == (failure.value.problem.happened, failure.value.problem.action)
    assert failed.error == "LeftoverInstallError"
    assert failed.levelname == "WARNING"
