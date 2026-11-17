"""The TestFlight path must refuse missing widget configuration before export."""

import importlib.util
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "widget_configuration", Path(__file__).resolve().parents[3] / "apple/scripts/widget_configuration.py"
)
assert _spec
assert _spec.loader
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

pytestmark = pytest.mark.unit
REVISION = "a" * 40
SIGNED_ENTITLEMENTS = guard.signed_entitlements


@pytest.mark.skipif(sys.platform != "darwin", reason="Apple project parsing uses plutil")
@pytest.mark.parametrize("changed", [None, guard.PROJECT, *(Path(path) for path, _ in guard.TARGETS.values())])
def test_source_rejects_each_file_from_the_footer_omission(tmp_path, changed):
    for file in [guard.PROJECT, guard.ENTITLEMENTS, *(Path(path) for path, _ in guard.TARGETS.values())]:
        destination = tmp_path / file
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(guard.APPLE / file, destination)
    if changed is None:
        guard.check_source(tmp_path)
    elif changed == guard.PROJECT:
        project = tmp_path / changed
        lines = project.read_text().splitlines(keepends=True)
        project.write_text("".join(line for line in lines if "INFOPLIST_FILE[sdk=iphone*]" not in line))
        with pytest.raises(ValueError, match=r"iOS Info.plist mapping differs"):
            guard.check_source(tmp_path)
    else:
        (tmp_path / changed).unlink()
        with pytest.raises(FileNotFoundError):
            guard.check_source(tmp_path)


def write_plist(path, values):
    path.write_bytes(plistlib.dumps(values))


@pytest.mark.skipif(sys.platform != "darwin", reason="Apple project parsing uses plutil")
@pytest.mark.parametrize(
    ("target", "modes"),
    [
        ("App/iOS-Info.plist", None),
        ("App/iOS-Info.plist", ["audio"]),
        ("App/iOS-Info.plist", ["screen-capture", "audio"]),
        ("Widgets/iOS-Info.plist", ["screen-capture"]),
    ],
)
def test_source_refuses_wrong_background_modes(tmp_path, target, modes):
    for file in [guard.PROJECT, guard.ENTITLEMENTS, *(Path(path) for path, _ in guard.TARGETS.values())]:
        destination = tmp_path / file
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(guard.APPLE / file, destination)
    path = tmp_path / target
    info = guard.plist(path)
    if modes is None:
        info.pop("UIBackgroundModes")
    else:
        info["UIBackgroundModes"] = modes
    write_plist(path, info)
    with pytest.raises(ValueError, match="background capture modes"):
        guard.check_source(tmp_path)


@pytest.fixture
def signed_archive(tmp_path, monkeypatch):
    app = tmp_path / "Products/Applications/Heinzel.app"
    widget = app / "PlugIns/HeinzelWidgets.appex"
    signed = {}
    for bundle, (_, identifier) in zip((app, widget), guard.TARGETS.values(), strict=True):
        bundle.mkdir(parents=True, exist_ok=True)
        private = f"ABCDEFGHIJ.{identifier}"
        shared = f"ABCDEFGHIJ.{guard.SHARED_KEYCHAIN}"
        info = {
            "CFBundleIdentifier": identifier,
            "CFBundleVersion": "28",
            guard.SOURCE_REVISION: REVISION,
            "HeinzelWidgetEnrollmentEnabled": True,
            "HeinzelWidgetKeychainGroup": shared,
        }
        if bundle == widget:
            info["NSExtension"] = {"NSExtensionPointIdentifier": "com.apple.widgetkit-extension"}
        else:
            info["UIBackgroundModes"] = ["screen-capture"]
        write_plist(bundle / "Info.plist", info)
        signed[bundle] = {
            "application-identifier": private,
            "com.apple.developer.team-identifier": "ABCDEFGHIJ",
            guard.GROUPS: [guard.APP_GROUP],
            guard.KEYCHAIN: [private, shared],
        }
    monkeypatch.setattr(guard, "signed_entitlements", signed.__getitem__)
    return tmp_path, app, widget, signed


@pytest.mark.parametrize(
    ("origin", "key", "value", "message"),
    [
        ("app", "CFBundleVersion", "26", "signed build number"),
        ("widget", "CFBundleVersion", "26", "signed build number"),
        ("app", guard.SOURCE_REVISION, "", "signed source revision"),
        ("widget", guard.SOURCE_REVISION, None, "signed source revision"),
        ("widget", "HeinzelWidgetEnrollmentEnabled", "YES", "enable widget enrollment"),
        ("widget", "HeinzelWidgetKeychainGroup", "team.foreign", "widget Keychain group"),
        ("app", "HeinzelWidgetEnrollmentEnabled", None, "enable widget enrollment"),
        ("app", "UIBackgroundModes", None, "background capture modes"),
        ("app", "UIBackgroundModes", [], "background capture modes"),
        ("app", "UIBackgroundModes", "screen-capture", "background capture modes"),
        ("app", "UIBackgroundModes", ["screen-capture", "audio"], "background capture modes"),
        ("widget", "UIBackgroundModes", ["screen-capture"], "background capture modes"),
        ("widget", "UIBackgroundModes", [], "background capture modes"),
        ("signed", guard.KEYCHAIN, [], "Signed private and shared Keychain"),
        ("signed", guard.GROUPS, [], "Signed widget App Group"),
        ("signed", "com.apple.developer.team-identifier", "ZYXWVUTSRQ", "share a signing team"),
    ],
)
def test_archive_refuses_wrong_metadata_or_entitlements(signed_archive, origin, key, value, message):
    tmp_path, app, widget, signed = signed_archive
    guard.check_archive(tmp_path, "28", REVISION)
    if origin == "signed":
        signed[widget][key] = value
    else:
        info_path = (app if origin == "app" else widget) / "Info.plist"
        info = guard.plist(info_path)
        if value is None:
            info.pop(key)
        else:
            info[key] = value
        write_plist(info_path, info)
    with pytest.raises(ValueError, match=message):
        guard.check_archive(tmp_path, "28", REVISION)


@pytest.mark.parametrize("numbering", [None, True, False])
def test_upload_only_runs_after_archive_and_fixed_build_checks(tmp_path, signed_archive, monkeypatch, numbering):
    calls = []
    options = tmp_path / "export.plist"
    settings = {"destination": "upload", "method": "app-store-connect"}
    if numbering is not None:
        settings["manageAppVersionAndBuildNumber"] = numbering
    write_plist(options, settings)
    monkeypatch.setattr(guard, "check_source", calls.append)
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)))
    destination = tmp_path / "upload"
    if numbering is not False:
        with pytest.raises(ValueError, match="preserve the archive build"):
            guard.upload(tmp_path, options, destination, "28", REVISION)
        assert calls == []
        return

    with pytest.raises(ValueError, match="signed build number"):
        guard.upload(tmp_path, options, destination, "26", REVISION)
    assert calls == [guard.APPLE]
    write_plist(options, {**settings, "teamID": "ZYXWVUTSRQ"})
    with pytest.raises(ValueError, match="export team differs"):
        guard.upload(tmp_path, options, destination, "28", REVISION)
    assert calls == [guard.APPLE, guard.APPLE]
    write_plist(options, {**settings, "teamID": "ABCDEFGHIJ"})
    guard.upload(tmp_path, options, destination, "28", REVISION)
    assert calls[:3] == [guard.APPLE] * 3
    assert calls[3] == (
        [
            "/usr/bin/xcodebuild",
            "-exportArchive",
            "-archivePath",
            str(tmp_path),
            "-exportOptionsPlist",
            str(options),
            "-exportPath",
            str(destination),
        ],
        {"check": True, "timeout": 1200},
    )


def test_invalid_signature_stops_upload_before_entitlement_read(tmp_path, signed_archive, monkeypatch):
    _, app, _, _ = signed_archive
    options = tmp_path / "export.plist"
    write_plist(
        options,
        {
            "destination": "upload",
            "method": "app-store-connect",
            "manageAppVersionAndBuildNumber": False,
        },
    )
    calls = []

    def signature_failure(command, **_kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(guard, "check_source", lambda _path: None)
    monkeypatch.setattr(guard, "signed_entitlements", SIGNED_ENTITLEMENTS)
    monkeypatch.setattr(subprocess, "run", signature_failure)
    with pytest.raises(subprocess.CalledProcessError):
        guard.upload(tmp_path, options, tmp_path / "upload", "28", REVISION)
    assert calls == [["/usr/bin/codesign", "--verify", "--strict", str(app)]]


def test_missing_capture_mode_prevents_export(signed_archive, monkeypatch):
    root, app, _, _ = signed_archive
    path = app / "Info.plist"
    info = guard.plist(path)
    info.pop("UIBackgroundModes")
    write_plist(path, info)
    options = root / "export.plist"
    write_plist(
        options, {"destination": "upload", "method": "app-store-connect", "manageAppVersionAndBuildNumber": False}
    )
    calls = []
    monkeypatch.setattr(guard, "check_source", lambda _path: None)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    with pytest.raises(ValueError, match="background capture modes"):
        guard.upload(root, options, root / "export", "28", REVISION)
    assert calls == []
