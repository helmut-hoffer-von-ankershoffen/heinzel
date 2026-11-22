"""Compile and run the native menu's routing probe without launching the app."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [
    pytest.mark.integration,
    pytest.mark.macos_specific,
    pytest.mark.skipif(sys.platform != "darwin", reason="The probe is compiled with the macOS Swift toolchain"),
    pytest.mark.timeout(75),
]


def test_mac_menu_routes_and_availability(tmp_path: Path) -> None:
    probe = tmp_path / "menu-state"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-swift-version",
            "6",
            "-enable-bare-slash-regex",
            str(ROOT / "apple/Shared/NativeRoute.swift"),
            str(ROOT / "tests/fixtures/apple-menu-state.swift"),
            "-o",
            str(probe),
        ],
        check=True,
        timeout=60,
    )
    subprocess.run([str(probe)], check=True, timeout=10)
