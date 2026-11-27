"""Mac widget refresh requires authorization in both signatures and profiles."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
_spec = importlib.util.spec_from_file_location(
    "mac_widget_signing", Path(__file__).resolve().parents[3] / "apple/scripts/verify-macos-cloud.py"
)
assert _spec
assert _spec.loader
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


@pytest.mark.parametrize("bundle", [guard.APP, guard.WIDGET])
@pytest.mark.parametrize("missing", [None, "authorization", "signature", "network", "metadata"])
def test_refresh_requires_paired_capabilities(bundle, missing):
    now = datetime.now(UTC)
    team = "ABCDEFGHIJ"
    identity = f"{team}.{bundle}"
    shared = f"{team}.team.heinzel.widget"
    groups = [f"{team}.team.heinzel.apple.activity"]
    signed = {
        guard.APP_ID: identity,
        guard.GROUPS: groups,
        guard.KEYCHAIN: [identity, shared],
        "com.apple.security.app-sandbox": True,
        "com.apple.security.network.client": True,
        "com.apple.security.device.audio-input": True,
    }
    authorized = {guard.APP_ID: identity, guard.KEYCHAIN: [identity, shared]}
    info = {
        "CFBundleIdentifier": bundle,
        "HeinzelWidgetEnrollmentEnabled": "YES",
        "HeinzelWidgetKeychainGroup": shared,
        "HeinzelWidgetGroup": groups[0],
    }
    profile = {
        "Platform": ["OSX"],
        "TeamIdentifier": [team],
        "ApplicationIdentifierPrefix": [team],
        "ExpirationDate": now + timedelta(days=1),
        "DeveloperCertificates": [b"fixture"],
        "Entitlements": authorized,
    }
    if missing == "authorization":
        authorized[guard.KEYCHAIN] = [identity]
    elif missing == "signature":
        signed[guard.KEYCHAIN] = [identity]
    elif missing == "network":
        signed.pop("com.apple.security.network.client")
    elif missing == "metadata":
        info["HeinzelWidgetEnrollmentEnabled"] = "NO"
    arguments = {
        "info": info,
        "profile": profile,
        "signed": signed,
        "certificate": b"fixture",
        "now": now,
        "widget_refresh": True,
    }
    if missing is None or (missing == "network" and bundle == guard.APP):
        guard.verify_metadata(bundle, team, **arguments)
    else:
        with pytest.raises(guard.ConfigurationError):
            guard.verify_metadata(bundle, team, **arguments)
