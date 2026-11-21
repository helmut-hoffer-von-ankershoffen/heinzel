"""Run the native footer geometry guard on macOS; simulator acceptance remains separate."""

import subprocess
import sys
from pathlib import Path

import pytest

from heinzel.testing import scaled

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(sys.platform != "darwin", reason="Swift/AppKit fixture requires macOS")
def test_native_footer_rejects_untrusted_or_stale_geometry(tmp_path: Path) -> None:
    binary = tmp_path / "footer-geometry"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-parse-as-library",
            "-swift-version",
            "6",
            "apple/App/BrowserNetwork.swift",
            "apple/App/Endpoint.swift",
            "apple/App/WorkspaceDirectory.swift",
            "apple/tests/WorkspaceFooterGeometryTests.swift",
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        check=True,
        timeout=scaled(120),
    )
    subprocess.run(["codesign", "--force", "--sign", "-", str(binary)], check=True, timeout=scaled(20))
    subprocess.run(["codesign", "--verify", "--strict", str(binary)], check=True, timeout=scaled(20))
    result = subprocess.run([str(binary)], capture_output=True, text=True, check=True, timeout=scaled(20))
    assert "PASS: native footer slot is bounded" in result.stdout
    assert "PASS: version refresh preserves offline cache and rejects superseded workspaces" in result.stdout
