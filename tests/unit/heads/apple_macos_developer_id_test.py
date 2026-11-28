"""The public Mac widget requires the release Team's local App Group in both signatures."""

import importlib.util
from pathlib import Path

import pytest

APPLE = Path(__file__).resolve().parents[3] / "apple"
SPEC = importlib.util.spec_from_file_location("developer_id", APPLE / "scripts/verify-macos-developer-id.py")
assert SPEC
assert SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)
pytestmark = pytest.mark.unit
TEAM = "ABCDEFGHIJ"
VERSION = "0.9.58"
REVISION = "a" * 40
GROUP = f"{TEAM}.team.heinzel.apple.activity"


@pytest.fixture(params=(guard.APP, guard.WIDGET), ids=("host", "widget"))
def metadata(request):
    bundle_id = request.param
    info = {
        "CFBundleIdentifier": bundle_id,
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": "1",
        "HeinzelSourceRevision": REVISION,
        "HeinzelWidgetGroup": GROUP,
        "HeinzelManagedInstall": "YES",
        "HeinzelCloudSignIn": "NO",
        "HeinzelWidgetEnrollmentEnabled": "NO",
        "NSExtension": {"NSExtensionPointIdentifier": "com.apple.widgetkit-extension"},
    }
    signed = {guard.GROUPS: [GROUP], "com.apple.security.app-sandbox": True}
    return bundle_id, info, signed


def test_accepts_exact_mac_widget_group_in_each_release_bundle(metadata):
    bundle_id, info, signed = metadata
    assert guard.verify_metadata(bundle_id, TEAM, VERSION, REVISION, info=info, signed=signed) == "1"


@pytest.mark.parametrize(
    ("area", "field", "value", "message"),
    [
        ("signed", guard.GROUPS, [], "signed Mac App Group"),
        ("signed", guard.GROUPS, ["group.team.heinzel.apple.activity"], "signed Mac App Group"),
        ("signed", guard.GROUPS, ["KLMNOPQRST.team.heinzel.apple.activity"], "signed Mac App Group"),
        ("signed", "com.apple.developer.team-identifier", "KLMNOPQRST", "signing team"),
        ("signed", "com.apple.security.get-task-allow", True, "debug entitlement"),
        ("info", "HeinzelWidgetGroup", "wrong", "runtime Mac App Group"),
        ("info", "HeinzelSourceRevision", "b" * 40, "source revision"),
        ("info", "CFBundleShortVersionString", "0.9.57", "marketing version"),
        ("info", "HeinzelWidgetEnrollmentEnabled", "YES", "credential enrollment"),
    ],
)
def test_refuses_stale_or_unauthorized_distribution_metadata(metadata, area, field, value, message):
    bundle_id, info, signed = metadata
    (signed if area == "signed" else info)[field] = value
    with pytest.raises(ValueError, match=message):
        guard.verify_metadata(bundle_id, TEAM, VERSION, REVISION, info=info, signed=signed)


def test_notary_acceptance_and_extracted_verification_precede_archive_promotion():
    script = (APPLE / "scripts/package-macos").read_text()
    submit = script.index("notarytool submit")
    accepted = script.index("== Accepted", submit)
    staple = script.index("stapler staple", accepted)
    extract = script.index('tar -xzf "$candidate"', staple)
    verify = script.index('verify-macos-developer-id.py "$archived_app"', extract)
    stapled = script.index('stapler validate "$archived_app"', verify)
    gatekeeper = script.index('spctl --assess --type execute "$archived_app"', stapled)
    assert gatekeeper < script.index('mv -f -- "$candidate" "$archive"')
