"""The Mac package must not advertise unsupported HTTPS association."""

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
SCRIPT = Path(__file__).resolve().parents[3] / "apple" / "scripts" / "package-macos"


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
    result = subprocess.run([str(SCRIPT), "1.2.3"], env=env, capture_output=True, check=False)
    assert result.returncode == 42
    assert "HEINZEL_HTTPS_LINKS=NO" in args.read_text().splitlines()


def test_explicit_https_refused_before_build():
    env = {**os.environ, "HEINZEL_HTTPS_LINKS": "YES", "HEINZEL_MAC_SIGNING_TEAM": "ABCDEFGHIJ"}
    result = subprocess.run([str(SCRIPT), "1.2.3"], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "Associated Domains" in result.stderr
