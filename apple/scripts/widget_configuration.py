"""Verify the iOS widget contract in source and on the TestFlight upload path."""

# ruff: noqa: INP001 - standalone Apple tooling

import argparse
import json
import plistlib
import re
import subprocess
from pathlib import Path
from typing import Any, cast

APPLE = Path(__file__).resolve().parents[1]
PROJECT = Path("Heinzel.xcodeproj/project.pbxproj")
TARGETS = {
    "Heinzel": ("App/iOS-Info.plist", "team.heinzel.apple"),
    "HeinzelWidgets": ("Widgets/iOS-Info.plist", "team.heinzel.apple.widgets"),
}
APP_GROUP = "group.team.heinzel.apple.activity"
SHARED_KEYCHAIN = "team.heinzel.widget"
KEYCHAIN = "keychain-access-groups"
GROUPS = "com.apple.security.application-groups"
ENTITLEMENTS = Path("Shared/WidgetActivity.entitlements")
SOURCE_REVISION = "HeinzelSourceRevision"


class ConfigurationError(ValueError):
    """A required setting or signed bundle property is missing or wrong."""


def require(condition: bool, problem: str) -> None:
    """Fail closed with the relevant configuration field.

    Raises:
        ConfigurationError: The check failed.
    """
    if not condition:
        raise ConfigurationError(problem)


def plist(path: Path) -> dict[str, Any]:
    """Read one property list dictionary.

    Raises:
        ConfigurationError: The property list is not a dictionary.
    """
    with path.open("rb") as stream:
        data = plistlib.load(stream)
    if isinstance(data, dict):
        return cast("dict[str, Any]", data)
    msg = f"{path.name} must contain a property list dictionary"
    raise ConfigurationError(msg)


def check_info(info: dict[str, Any], keychain: str, *, widget: bool) -> None:
    """Require enrollment, shared Keychain group and app-only background capture."""
    role = "Widget" if widget else "App"
    require(
        "UIBackgroundModes" not in info if widget else info.get("UIBackgroundModes") == ["screen-capture"],
        f"{role} background capture modes differ",
    )
    require(info.get("HeinzelWidgetEnrollmentEnabled") is True, f"{role} must enable widget enrollment")
    require(info.get("HeinzelWidgetKeychainGroup") == keychain, f"{role} widget Keychain group differs")
    if widget:
        extension = info.get("NSExtension")
        require(
            isinstance(extension, dict)
            and cast("dict[str, Any]", extension).get("NSExtensionPointIdentifier") == "com.apple.widgetkit-extension",
            "Widget extension must use the WidgetKit extension point",
        )


def output(command: list[str]) -> bytes:
    """Read output from a fixed macOS tool."""
    result = subprocess.run(command, capture_output=True, check=True, timeout=60)  # noqa: S603
    return result.stdout


def check_source(apple: Path) -> None:
    """Verify the exact iOS settings for each app and widget build configuration."""
    objects = json.loads(output(["/usr/bin/plutil", "-convert", "json", "-o", "-", str(apple / PROJECT)]))["objects"]
    for name, (path, identifier) in TARGETS.items():
        (target,) = [obj for obj in objects.values() if obj.get("isa") == "PBXNativeTarget" and obj.get("name") == name]
        configs = objects[target["buildConfigurationList"]]["buildConfigurations"]
        require({objects[item]["name"] for item in configs} == {"Debug", "Release"}, f"{name} configurations differ")
        for item in configs:
            settings = objects[item]["buildSettings"]
            require(settings.get("INFOPLIST_FILE[sdk=iphone*]") == path, f"{name} iOS Info.plist mapping differs")
            require(settings.get("PRODUCT_BUNDLE_IDENTIFIER") == identifier, f"{name} bundle identifier differs")
            if name == "Heinzel":
                require(
                    settings.get("CODE_SIGN_ENTITLEMENTS[sdk=iphone*]") == "$(HEINZEL_IOS_ENTITLEMENTS)"
                    and settings.get("HEINZEL_IOS_ENTITLEMENTS") == str(ENTITLEMENTS),
                    "App iOS entitlements mapping differs",
                )
            else:
                require(
                    settings.get("CODE_SIGN_ENTITLEMENTS[sdk=iphone*]", settings.get("CODE_SIGN_ENTITLEMENTS"))
                    == str(ENTITLEMENTS),
                    "Widget iOS entitlements mapping differs",
                )
        info = plist(apple / path)
        require(info.get(SOURCE_REVISION) == "$(HEINZEL_SOURCE_REVISION)", f"{name} source revision mapping differs")
        check_info(
            info,
            f"$(AppIdentifierPrefix){SHARED_KEYCHAIN}",
            widget=name == "HeinzelWidgets",
        )
    entitlements = plist(apple / ENTITLEMENTS)
    require(entitlements.get(GROUPS) == [APP_GROUP], "Source widget App Group differs")
    require(
        entitlements.get(KEYCHAIN)
        == ["$(AppIdentifierPrefix)$(PRODUCT_BUNDLE_IDENTIFIER)", f"$(AppIdentifierPrefix){SHARED_KEYCHAIN}"],
        "Source widget Keychain groups differ",
    )


def signed_entitlements(bundle: Path) -> dict[str, Any]:
    """Read actual entitlements after strict signature verification.

    Raises:
        ConfigurationError: The entitlements are not a dictionary.
    """
    output(["/usr/bin/codesign", "--verify", "--strict", str(bundle)])
    data = plistlib.loads(output(["/usr/bin/codesign", "--display", "--entitlements", ":-", str(bundle)]))
    if isinstance(data, dict):
        return cast("dict[str, Any]", data)
    msg = "Signed bundle entitlements must be a dictionary"
    raise ConfigurationError(msg)


def check_archive(archive: Path, build: str, revision: str) -> str:
    """Verify both signed products and the requested build and source metadata."""
    require(re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,2}", build) is not None, "Expected an explicit build number")
    require(re.fullmatch(r"[a-f0-9]{40}", revision) is not None, "Expected an exact source commit")
    app = archive / "Products/Applications/Heinzel.app"
    widget = app / "PlugIns/HeinzelWidgets.appex"
    identities: set[tuple[str, str]] = set()
    for bundle, (_, identifier) in zip((app, widget), TARGETS.values(), strict=True):
        info = plist(bundle / "Info.plist")
        signed = signed_entitlements(bundle)
        require(info.get("CFBundleVersion") == build, f"{bundle.name} signed build number differs")
        require(info.get(SOURCE_REVISION) == revision, f"{bundle.name} signed source revision differs")
        app_id = signed.get("application-identifier")
        team = signed.get("com.apple.developer.team-identifier")
        prefix = app_id.partition(".")[0] if isinstance(app_id, str) else ""
        require(
            isinstance(team, str)
            and re.fullmatch(r"[A-Z0-9]{10}", team) is not None
            and re.fullmatch(r"[A-Z0-9]{10}", prefix) is not None
            and app_id == f"{prefix}.{identifier}"
            and info.get("CFBundleIdentifier") == identifier,
            "Archive bundle has an unexpected signed identity",
        )
        require(signed.get("get-task-allow", False) is False, "Archive bundle permits debugger attachment")
        shared = f"{prefix}.{SHARED_KEYCHAIN}"
        require(signed.get(KEYCHAIN) == [app_id, shared], "Signed private and shared Keychain groups differ")
        require(signed.get(GROUPS) == [APP_GROUP], "Signed widget App Group differs")
        check_info(info, shared, widget=bundle == widget)
        identities.add((cast("str", team), prefix))
    require(len(identities) == 1, "App and widget must share a signing team and identifier prefix")
    return identities.pop()[0]


def upload(archive: Path, options: Path, destination: Path, build: str, revision: str) -> None:
    """Run the existing Xcode export only after verifying its source and archive."""
    export = plist(options)
    require(export.get("destination") == "upload", "TestFlight export options must request upload")
    require(export.get("method") in ("app-store-connect", "app-store"), "TestFlight export requires App Store method")
    require(export.get("manageAppVersionAndBuildNumber") is False, "TestFlight export must preserve the archive build")
    check_source(APPLE)
    signing_team = check_archive(archive, build, revision)
    require(export.get("teamID", signing_team) == signing_team, "TestFlight export team differs from archive")
    subprocess.run(  # noqa: S603
        [
            "/usr/bin/xcodebuild",
            "-exportArchive",
            "-archivePath",
            str(archive),
            "-exportOptionsPlist",
            str(options),
            "-exportPath",
            str(destination),
        ],
        check=True,
        timeout=1200,
    )


def main() -> None:
    """Run the requested unsigned-source, signed-archive, or upload check."""
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser("source")
    verify = modes.add_parser("archive")
    publish = modes.add_parser("upload")
    for operation in (verify, publish):
        operation.add_argument("archive", type=Path)
        operation.add_argument("--build", required=True)
        operation.add_argument("--revision", required=True)
    publish.add_argument("export_options", type=Path)
    publish.add_argument("export_path", type=Path)
    args = parser.parse_args()
    try:
        if args.mode == "source":
            check_source(APPLE)
        elif args.mode == "archive":
            check_archive(args.archive, args.build, args.revision)
        else:
            upload(args.archive, args.export_options, args.export_path, args.build, args.revision)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Widget configuration check failed: {error}\n")
    print(f"Widget configuration {args.mode} passed")  # noqa: T201 - CLI result


if __name__ == "__main__":
    main()
