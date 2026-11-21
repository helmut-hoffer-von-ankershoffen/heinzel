"""The Mac package must not advertise unsupported HTTPS association."""

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
    }
    result = subprocess.run([str(SCRIPT)], env=env, capture_output=True, check=False, timeout=30)
    assert result.returncode == 42
    built = args.read_text().splitlines()
    assert "HEINZEL_HTTPS_LINKS=NO" in built
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
