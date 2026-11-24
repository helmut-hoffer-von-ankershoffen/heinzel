"""Mac package capability and signing choices without running Xcode."""

import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "apple" / "scripts" / "package-macos"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


@pytest.mark.parametrize("prefix", ["", "ABCDEFGHIJ"])
def test_site_prefix_does_not_enable_mac_https(tmp_path, prefix):
    xcode = tmp_path / "xcodebuild"
    xcode.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGS_FILE"\nexit 42\n')
    xcode.chmod(0o700)
    args = tmp_path / "args"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "ARGS_FILE": str(args),
        "APPLE_APPLICATION_IDENTIFIER_PREFIX": prefix,
        "HEINZEL_HTTPS_LINKS": "",
        "HEINZEL_ICLOUD_SIGN_IN": "",
    }
    result = subprocess.run([str(SCRIPT)], env=env, capture_output=True, check=False, timeout=30)
    assert result.returncode == 42
    built = args.read_text().splitlines()
    assert "HEINZEL_HTTPS_LINKS=NO" in built
    assert "HEINZEL_ICLOUD_SIGN_IN=NO" in built
    assert f"MARKETING_VERSION={VERSION}" in built
    assert any(re.fullmatch(r"HEINZEL_SOURCE_REVISION=[0-9a-f]{40}", arg) for arg in built)


def test_version_other_than_pyproject_refused_before_build():
    result = subprocess.run([str(SCRIPT), "1.2.3"], capture_output=True, text=True, check=False, timeout=30)
    assert result.returncode == 2
    assert f"differs from pyproject.toml ({VERSION})" in result.stderr


def test_explicit_https_refused_before_build():
    env = {**os.environ, "HEINZEL_HTTPS_LINKS": "YES", "HEINZEL_MAC_SIGNING_TEAM": "ABCDEFGHIJ"}
    result = subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True, check=False, timeout=30)
    assert result.returncode == 2
    assert "Associated Domains" in result.stderr


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"HEINZEL_MAC_SIGNING_TEAM": ""},
        {"HEINZEL_MAC_PROFILE": ""},
        {"HEINZEL_MAC_WIDGET_PROFILE": ""},
        {"HEINZEL_MAC_WIDGET_PROFILE": "Fixture Mac app"},
    ],
    ids=["complete", "team-missing", "app-profile-missing", "widget-profile-missing", "same-profile"],
)
def test_icloud_mac_packaging_requires_separate_profiles_and_passes_them_to_xcode(tmp_path, overrides):
    xcode = tmp_path / "xcodebuild"
    xcode.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGS_FILE"\nexit 42\n')
    xcode.chmod(0o700)
    args = tmp_path / "args"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "ARGS_FILE": str(args),
        "HEINZEL_HTTPS_LINKS": "NO",
        "HEINZEL_ICLOUD_SIGN_IN": "YES",
        "HEINZEL_MAC_SIGNING_TEAM": "ABCDEFGHIJ",
        "HEINZEL_MAC_PROFILE": "Fixture Mac app",
        "HEINZEL_MAC_WIDGET_PROFILE": "Fixture widget",
        **overrides,
    }
    result = subprocess.run([str(SCRIPT)], env=env, capture_output=True, check=False, timeout=30)
    if overrides:
        assert result.returncode == 2
        assert b"requires" in result.stderr
        assert not args.exists()
        return
    assert result.returncode == 42
    assert {
        "DEVELOPMENT_TEAM=ABCDEFGHIJ",
        "CODE_SIGN_STYLE=Manual",
        "CODE_SIGN_IDENTITY=Apple Development",
        "HEINZEL_ICLOUD_SIGN_IN=YES",
        "HEINZEL_MAC_ENTITLEMENTS=App/MacCloudSignIn.entitlements",
        "HEINZEL_MAC_WIDGET_ENTITLEMENTS=Widgets/MacWidgetGroup.entitlements",
        "HEINZEL_MAC_PROFILE=Fixture Mac app",
        "HEINZEL_MAC_WIDGET_PROFILE=Fixture widget",
    } <= set(args.read_text().splitlines())
