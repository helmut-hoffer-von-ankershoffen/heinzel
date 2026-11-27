# ruff: noqa: INP001 - standalone script run by macOS packaging
"""Verify the signing metadata of the re-extracted iCloud-capable macOS package."""

from __future__ import annotations

import plistlib
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

APP = "team.heinzel.apple"
WIDGET = "team.heinzel.apple.widgets"
APP_ID = "com.apple.application-identifier"
TEAM_ID = "com.apple.developer.team-identifier"
GROUPS = "com.apple.security.application-groups"
KEYCHAIN = "keychain-access-groups"
UTC = timezone.utc  # noqa: UP017 - Apple system Python may predate datetime.UTC
Plist = dict[str, object]


class ConfigurationError(ValueError):
    """The profile or signature does not match the requested Mac app."""


def _require(condition: object, reason: str) -> None:
    if not condition:
        raise ConfigurationError(reason)


def _output(*command: str) -> bytes:
    return subprocess.run(command, capture_output=True, check=True, timeout=30).stdout  # noqa: S603 - fixed tools


def _allows_group(groups: object, expected: str) -> bool:
    return isinstance(groups, list) and any(
        isinstance(group, str) and (group == expected or (group.endswith(".*") and expected.startswith(group[:-1])))
        for group in groups
    )


def verify_metadata(  # noqa: PLR0913 - decoded inputs from the three tools and the expected signing identity
    bundle_id: str,
    team: str,
    *,
    info: Plist,
    profile: Plist,
    signed: Plist,
    certificate: bytes,
    now: datetime,
    widget_refresh: bool = False,
) -> None:
    """Compare the profile's authorization to the identity and claims in the signature.

    Raises:
        ConfigurationError: If the profile and signed product do not match.
    """
    _require(info.get("CFBundleIdentifier") == bundle_id, "bundle identifier differs")
    platforms = profile.get("Platform")
    _require(isinstance(platforms, list) and "OSX" in platforms, "profile does not authorize macOS")
    _require(profile.get("TeamIdentifier") == [team], "profile signing team differs")
    date = profile.get("ExpirationDate")
    _require(isinstance(date, datetime) and date.replace(tzinfo=UTC) > now, "profile is expired")
    certificates = profile.get("DeveloperCertificates")
    _require(
        isinstance(certificates, list) and certificate and certificate in certificates,
        "profile does not authorize the signing certificate",
    )

    authorized = profile.get("Entitlements")
    if not isinstance(authorized, dict):
        msg = "profile has no entitlement authorization"
        raise ConfigurationError(msg)
    app_id = signed.get(APP_ID)
    prefix = app_id.partition(".")[0] if isinstance(app_id, str) else ""
    expected_id = f"{prefix}.{bundle_id}"
    prefixes = profile.get("ApplicationIdentifierPrefix")
    _require(
        re.fullmatch(r"[A-Z0-9]{10}", prefix)
        and isinstance(prefixes, list)
        and prefix in prefixes
        and app_id == expected_id
        and authorized.get(APP_ID, authorized.get("application-identifier")) == app_id
        and authorized.get("application-identifier", app_id) == app_id,
        "signed and authorized application identifiers differ",
    )
    for entitlements in (authorized, signed):
        _require(entitlements.get(TEAM_ID, team) == team, "entitlement signing team differs")
    _require(signed.get(GROUPS) == [f"{team}.team.heinzel.apple.activity"], "signed app group differs")
    if widget_refresh:
        shared = f"{prefix}.team.heinzel.widget"
        _require(info.get("HeinzelWidgetEnrollmentEnabled") == "YES", "widget enrollment differs")
        _require(info.get("HeinzelWidgetKeychainGroup") == shared, "widget Keychain metadata differs")
        _require(info.get("HeinzelWidgetGroup") == f"{team}.team.heinzel.apple.activity", "widget container differs")
        _require(
            signed.get(KEYCHAIN) == [expected_id, shared]
            and all(_allows_group(authorized.get(KEYCHAIN), group) for group in (expected_id, shared)),
            "signed or authorized widget Keychain group differs",
        )
        if bundle_id == WIDGET:
            _require(signed.get("com.apple.security.network.client") is True, "widget network access differs")
    if bundle_id == APP:
        _require(
            widget_refresh
            or (signed.get(KEYCHAIN) == [expected_id] and _allows_group(authorized.get(KEYCHAIN), expected_id)),
            "signed or authorized Keychain group differs",
        )
        _require(signed.get("com.apple.security.device.audio-input") is True, "signed audio access differs")
    else:
        _require(signed.get("com.apple.security.app-sandbox") is True, "signed widget sandbox differs")


def _verify_bundle(bundle: Path, bundle_id: str, team: str, *, widget_refresh: bool = False) -> str:
    contents = bundle / "Contents"
    profile_file = contents / "embedded.provisionprofile"
    _require(profile_file.is_file() and profile_file.stat().st_size, "no embedded provisioning profile")
    info_file = contents / "Info.plist"
    for path in (profile_file, info_file):
        _require(
            not path.is_symlink() and path.resolve(strict=True).is_relative_to(bundle.resolve(strict=True)),
            "signing metadata escapes the bundle",
        )
    requirement = f'anchor apple generic and identifier "{bundle_id}" and certificate leaf[subject.OU] = "{team}"'
    _output("/usr/bin/codesign", "--verify", "--strict", f"-R={requirement}", str(bundle))
    profile = plistlib.loads(_output("/usr/bin/security", "cms", "-D", "-i", str(profile_file)))
    signed = plistlib.loads(_output("/usr/bin/codesign", "--display", "--entitlements", "-", "--xml", str(bundle)))
    with info_file.open("rb") as handle:
        info = plistlib.load(handle)
    _require(all(isinstance(item, dict) for item in (profile, signed, info)), "signing metadata is not a dictionary")
    with TemporaryDirectory() as folder:
        certificate = Path(folder) / "signer"
        _output("/usr/bin/codesign", "--display", f"--extract-certificates={certificate}", str(bundle))
        leaf = certificate.with_name("signer0").read_bytes()
    verify_metadata(
        bundle_id,
        team,
        info=info,
        profile=profile,
        signed=signed,
        certificate=leaf,
        now=datetime.now(UTC),
        widget_refresh=widget_refresh,
    )
    return str(signed[APP_ID]).partition(".")[0]


def main() -> None:
    """Refuse a package when either Mac target has mismatched signing metadata."""
    app_path, team, *refresh = sys.argv[1:]
    _require(refresh in ([], ["YES"], ["NO"]), "expected YES or NO for widget refresh")
    _require(re.fullmatch(r"[A-Z0-9]{10}", team), "expected a signing team")
    app = Path(app_path)
    widget = app / "Contents/PlugIns/HeinzelWidgets.appex"
    prefixes: set[str] = set()
    for bundle, bundle_id in ((app, APP), (widget, WIDGET)):
        try:
            _require(
                not bundle.is_symlink() and bundle.resolve(strict=True).is_relative_to(app.resolve(strict=True)),
                "signed product escapes the archive's app",
            )
            prefixes.add(_verify_bundle(bundle, bundle_id, team, widget_refresh=refresh == ["YES"]))
        except ConfigurationError as exc:
            sys.exit(f"{bundle_id}: {exc}")
        except (OSError, ValueError, plistlib.InvalidFileException, subprocess.SubprocessError):
            sys.exit(f"{bundle_id}: could not read or verify the signing metadata")
    _require(len(prefixes) == 1, "app and widget signing prefixes differ")


if __name__ == "__main__":
    main()
