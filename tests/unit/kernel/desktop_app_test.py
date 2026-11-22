"""Which desktop app is in Applications, the `native`/`electron` names and their old alias, and the quiet terminal."""

import logging
import plistlib

import pytest
import typer

from heinzel import HeinzelError
from heinzel.heads._desktop_release import DesktopFlavor, platform_flavor
from heinzel.heads.cli import people_only
from heinzel.heads.cli._app import parse_flavor
from heinzel.kernel import _desktop_app
from heinzel.kernel._desktop_app import app_hint, installed_desktop_app, label, read_bundle

pytestmark = pytest.mark.unit


def _bundle(folder, bundle_id, version="0.9.53", executable="Heinzel"):
    bundle = folder / _desktop_app.BUNDLE_NAME
    (bundle / "Contents").mkdir(parents=True)
    info = {"CFBundleIdentifier": bundle_id, "CFBundleShortVersionString": version, "CFBundleExecutable": executable}
    (bundle / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    return bundle


@pytest.mark.parametrize(
    ("bundle_id", "variant", "shown"),
    [
        ("team.heinzel.apple", "native", "Native app 0.9.53"),
        ("org.heinzelmaennchen.desktop", "electron", "Electron app 0.9.53"),
        ("com.example.other", None, "Desktop app 0.9.53"),
    ],
)
def test_the_bundle_id_tells_the_variant(tmp_path, bundle_id, variant, shown):
    found = read_bundle(_bundle(tmp_path, bundle_id))
    assert found is not None
    assert (found.variant, found.bundle_id, found.version, found.label) == (variant, bundle_id, "0.9.53", shown)


def test_a_missing_bundle_a_symlink_and_an_unreadable_plist(tmp_path):
    assert read_bundle(tmp_path / "absent.app") is None
    link = tmp_path / "link.app"
    link.symlink_to(tmp_path)
    assert read_bundle(link).symlink is True
    broken = tmp_path / "broken.app"
    (broken / "Contents").mkdir(parents=True)
    (broken / "Contents" / "Info.plist").write_text("not a plist")
    assert (read_bundle(broken).variant, read_bundle(broken).label) == (None, "Desktop app")


def test_system_applications_comes_before_the_users_folder(tmp_path):
    system, home = tmp_path / "system", tmp_path / "home"
    system.mkdir()
    (home / "Applications").mkdir(parents=True)
    assert installed_desktop_app(home, system) is None
    _bundle(home / "Applications", "org.heinzelmaennchen.desktop")
    assert installed_desktop_app(home, system).variant == "electron"
    _bundle(system, "team.heinzel.apple")
    assert installed_desktop_app(home, system).variant == "native"


def test_labels_and_the_switch_hint():
    assert (label("native"), label("electron"), label(None)) == ("Native app", "Electron app", "Desktop app")
    assert app_hint(type("Head", (), {"variant": "electron"})()).endswith("switches to the Native app")
    assert app_hint(type("Head", (), {"variant": "native"})()) == ""


def test_flavor_is_native_or_electron_and_swift_still_means_native():
    assert [flavor.value for flavor in DesktopFlavor] == ["electron", "native"]
    assert parse_flavor("native") is DesktopFlavor.NATIVE
    assert parse_flavor("swift") is DesktopFlavor.NATIVE  # hidden alias: old scripts keep working
    assert parse_flavor("Electron") is DesktopFlavor.ELECTRON
    assert DesktopFlavor("swift") is DesktopFlavor.NATIVE  # an old install record
    with pytest.raises(typer.BadParameter, match="not one of native, electron"):
        parse_flavor("qt")


def test_native_is_the_mac_default_and_linux_has_only_electron():
    assert platform_flavor(None, "darwin") is None  # the installer picks: the installed app's flavor, else native
    assert platform_flavor(DesktopFlavor.ELECTRON, "darwin") is DesktopFlavor.ELECTRON
    assert platform_flavor(None, "linux") is DesktopFlavor.ELECTRON
    assert platform_flavor(DesktopFlavor.ELECTRON, "linux") is DesktopFlavor.ELECTRON
    with pytest.raises(HeinzelError, match="The native app is macOS only"):
        platform_flavor(DesktopFlavor.NATIVE, "linux")


def test_structured_events_stay_off_the_terminal_at_the_default_level():
    event = logging.LogRecord("operation", logging.WARNING, __file__, 1, "operation.step.outcome", None, None)
    event.event = "operation.step.outcome"
    plain = logging.LogRecord("x", logging.WARNING, __file__, 1, "Something the person reads", None, None)
    assert people_only("WARNING")(event) is False
    assert people_only("WARNING")(plain) is True
    assert people_only("INFO")(event) is True  # asked for: every event shows
