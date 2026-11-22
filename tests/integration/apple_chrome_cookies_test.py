"""Read, decrypt and convert a fixture Chrome cookie database without a Keychain, a window or WebKit."""

import platform
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [
    pytest.mark.integration,
    pytest.mark.macos_specific,
    pytest.mark.skipif(sys.platform != "darwin", reason="The native importer uses macOS CommonCrypto and SQLite"),
    pytest.mark.timeout(120),
]


def test_native_importer_reads_a_fixture_profile(tmp_path):
    probe = tmp_path / "chrome-cookies"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-swift-version",
            "6",
            "-target",
            f"{platform.machine()}-apple-macosx15.0",
            str(ROOT / "apple/App/ChromeCookieImport.swift"),
            str(ROOT / "tests/fixtures/apple-chrome-cookies.swift"),
            "-o",
            str(probe),
        ],
        check=True,
        timeout=90,
    )
    result = subprocess.run([str(probe)], check=True, timeout=20, capture_output=True, text=True)
    assert result.stdout.strip() == "chrome cookies: ok"
