"""Exercise the native menu's routing without launching the app."""

import shutil
import subprocess
from pathlib import Path

import pytest

from heinzel.testing import scaled

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.unit


@pytest.mark.skipif(shutil.which("swiftc") is None, reason="Swift compiler unavailable")
@pytest.mark.timeout(75)
def test_mac_menu_routes_and_availability(tmp_path: Path) -> None:
    probe = tmp_path / "menu-state"
    subprocess.run(
        [
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
        timeout=scaled(55),
    )
    subprocess.run([str(probe)], check=True, timeout=scaled(10))
