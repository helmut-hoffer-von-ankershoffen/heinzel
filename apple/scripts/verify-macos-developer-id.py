# ruff: noqa: INP001 - standalone script run by macOS packaging
"""Verify the Developer ID-signed Mac host and its bundled WidgetKit extension."""

import argparse
import plistlib
import re
import subprocess
from pathlib import Path

APP = "team.heinzel.apple"
WIDGET = f"{APP}.widgets"
GROUPS = "com.apple.security.application-groups"


def require(condition: object, message: str) -> None:
    """Refuse an incomplete or mismatched release bundle.

    Raises:
        ValueError: If the release metadata fails the check.
    """
    if not condition:
        raise ValueError(message)


def verify_metadata(  # noqa: PLR0913 - independent release and bundle inputs
    bundle_id: str, team: str, version: str, revision: str, *, info: dict[str, object], signed: dict[str, object]
) -> str:
    """Check that signed App Group access agrees with the runtime metadata.

    Raises:
        ValueError: If the bundle metadata differs from the expected release.
    """
    group = f"{team}.{APP}.activity"
    require(info.get("CFBundleIdentifier") == bundle_id, "bundle identifier differs")
    require(info.get("CFBundleShortVersionString") == version, "marketing version differs")
    require(info.get("HeinzelSourceRevision") == revision, "source revision differs")
    build = info.get("CFBundleVersion")
    if not isinstance(build, str) or not build.isdecimal():
        msg = "build number is missing or invalid"
        raise ValueError(msg)
    require(info.get("HeinzelWidgetGroup") == group, "runtime Mac App Group differs")
    require(signed.get(GROUPS) == [group], "signed Mac App Group differs")
    require(signed.get("com.apple.developer.team-identifier", team) == team, "entitlement signing team differs")
    require(signed.get("com.apple.security.get-task-allow", False) is False, "debug entitlement is enabled")
    require(info.get("HeinzelWidgetEnrollmentEnabled") == "NO", "widget credential enrollment is enabled")
    if bundle_id == APP:
        require(info.get("HeinzelManagedInstall") == "YES", "not the managed desktop application")
        require(info.get("HeinzelCloudSignIn") == "NO", "iCloud sign-in is enabled")
    if bundle_id == WIDGET:
        extension = info.get("NSExtension")
        require(
            isinstance(extension, dict)
            and extension.get("NSExtensionPointIdentifier") == "com.apple.widgetkit-extension",
            "not a WidgetKit extension",
        )
        require(signed.get("com.apple.security.app-sandbox") is True, "WidgetKit extension is not sandboxed")
    return build


def verify_bundle(bundle: Path, bundle_id: str, team: str, version: str, revision: str) -> str:
    """Verify the certificate class, signing Team, hardened runtime, and bundle metadata."""
    bundle = bundle.resolve(strict=True)
    # This Apple leaf marker denotes a Developer ID Application certificate.
    requirement = (
        f'anchor apple generic and identifier "{bundle_id}" and certificate leaf[subject.OU] = "{team}"'
        " and certificate 1[field.1.2.840.113635.100.6.2.6] exists"
        " and certificate leaf[field.1.2.840.113635.100.6.1.13] exists"
    )
    subprocess.run(  # noqa: S603 - fixed verifier and options, absolute bundle path
        ("/usr/bin/codesign", "--verify", "--strict", f"-R={requirement}", "--", str(bundle)),
        capture_output=True,
        check=True,
        timeout=30,
    )
    details = subprocess.run(  # noqa: S603 - fixed verifier and options, absolute bundle path
        ("/usr/bin/codesign", "--display", "--verbose=4", "--", str(bundle)),
        capture_output=True,
        check=True,
        timeout=30,
    ).stderr
    require(re.search(rb"\bflags=0x[0-9a-f]+\([^\n)]*\bruntime\b", details), "hardened runtime is missing")
    info_path = bundle / "Contents/Info.plist"
    require(
        not info_path.is_symlink() and info_path.resolve(strict=True).is_relative_to(bundle.resolve(strict=True)),
        "bundle metadata escapes the app",
    )
    info = plistlib.loads(info_path.read_bytes())
    entitlements = subprocess.run(  # noqa: S603 - fixed verifier and options, absolute bundle path
        ("/usr/bin/codesign", "--display", "--entitlements", "-", "--xml", "--", str(bundle)),
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout
    signed = plistlib.loads(entitlements)
    require(isinstance(info, dict) and isinstance(signed, dict), "invalid bundle or entitlement metadata")
    return verify_metadata(bundle_id, team, version, revision, info=info, signed=signed)


def main() -> None:
    """Check both independently signed bundles in the exact release layout."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("team")
    parser.add_argument("version")
    parser.add_argument("revision")
    args = parser.parse_args()
    try:
        require(re.fullmatch(r"[A-Z0-9]{10}", args.team), "invalid signing Team")
        require(re.fullmatch(r"[0-9a-f]{40}", args.revision), "invalid source revision")
        widget = args.app / "Contents/PlugIns/HeinzelWidgets.appex"
        require(
            not args.app.is_symlink()
            and not widget.is_symlink()
            and widget.resolve(strict=True).is_relative_to(args.app.resolve(strict=True)),
            "WidgetKit extension is missing or escapes the app",
        )
        app_build = verify_bundle(args.app, APP, args.team, args.version, args.revision)
        widget_build = verify_bundle(widget, WIDGET, args.team, args.version, args.revision)
        require(app_build == widget_build, "host and widget build numbers differ")
    except (OSError, ValueError, plistlib.InvalidFileException, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Mac Developer ID verification failed: {exc}\n")


if __name__ == "__main__":
    main()
