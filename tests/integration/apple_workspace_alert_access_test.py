"""Verify that Apple connection diagnostics and alert delivery use the same permission decision."""

import subprocess
import sys
from pathlib import Path

import pytest

from heinzel.testing import scaled

pytestmark = [
    pytest.mark.integration,
    pytest.mark.macos_specific,
    pytest.mark.skipif(sys.platform != "darwin", reason="Requires the Apple UserNotifications framework"),
]


def test_workspace_alert_access(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    binary = tmp_path / "workspace-alert-access"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-parse-as-library",
            "-swift-version",
            "6",
            "apple/App/Endpoint.swift",
            "apple/App/BrowserNetwork.swift",
            "apple/App/WorkspaceNotifications.swift",
            "apple/tests/WorkspaceAlertAccessTests.swift",
            "-o",
            str(binary),
        ],
        cwd=root,
        check=True,
        timeout=scaled(90),
    )
    subprocess.run([str(binary)], check=True, timeout=scaled(15))
