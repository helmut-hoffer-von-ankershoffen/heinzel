"""Where the desktop app goes: /Applications when this user may write there, else ~/Applications, and migration.

Every case uses a fake home and a fake system Applications folder; bundles are inert files and nothing is launched.
"""

import os
import plistlib
import struct
import sys
from pathlib import Path

import pytest

from heinzel.heads import _desktop_installation as desktop

pytestmark = pytest.mark.unit


def _fetch(version: str, directory: Path) -> Path:
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


_REASONS = {"moved": "could not be moved", "admin": "administrator password"}


@pytest.mark.parametrize(
    ("folder", "usable", "reason"),
    [("system", True, None), ("home", True, "moved"), ("home", False, "admin")],
)
def test_the_install_message_gives_the_true_reason_the_app_is_not_in_applications(
    monkeypatch, tmp_path, folder, usable, reason
):
    import io

    from rich.console import Console

    from heinzel.heads.cli import _app

    system = tmp_path / "system-applications"
    where = system if folder == "system" else tmp_path / "home" / "Applications"
    monkeypatch.setattr(desktop, "SYSTEM_APPLICATIONS", system)
    monkeypatch.setattr(desktop, "desktop_location", lambda: where / desktop.BUNDLE_NAME)
    monkeypatch.setattr(desktop, "system_applications_usable", lambda: usable)
    out = io.StringIO()
    _app._print_location(Console(file=out, width=400), Console(file=io.StringIO(), width=400))
    text = out.getvalue()
    assert f"installed at {where / desktop.BUNDLE_NAME}" in text
    for key, words in _REASONS.items():
        assert (words in text) is (key == reason)
