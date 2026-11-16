"""Exercise the guest wipe coordinator without a window, network or WebKit data store."""

import platform
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [
    pytest.mark.integration,
    pytest.mark.macos_specific,
    pytest.mark.skipif(sys.platform != "darwin", reason="The browser uses macOS WebKit"),
    pytest.mark.timeout(75),
]


def test_guest_clear_serializes_detach_failure_and_retry(tmp_path):
    probe = tmp_path / "browser-clear"
    app = ROOT / "apple/App"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-swift-version",
            "6",
            "-target",
            f"{platform.machine()}-apple-macosx15.0",
            *(str(app / f"{name}.swift") for name in ("Endpoint", "BrowserNetwork", "IntegratedBrowser")),
            str(ROOT / "tests/fixtures/apple-browser-clear.swift"),
            "-o",
            str(probe),
        ],
        check=True,
        timeout=60,
    )
    subprocess.run([str(probe)], check=True, timeout=10)
