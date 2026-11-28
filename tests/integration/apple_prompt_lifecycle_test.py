"""Native prompt state survives a dismissed sheet while its answer is suspended."""

import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

from heinzel.testing import scaled

pytestmark = [
    pytest.mark.real_system,
    pytest.mark.integration,
    pytest.mark.macos_specific,
    pytest.mark.skipif(sys.platform != "darwin", reason="The Swift app requires macOS"),
]
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.timeout(75)
def test_prompt_sheet_and_suspended_send_lifecycle(tmp_path):
    app = (ROOT / "apple/App/HeinzelApp.swift").read_text()
    sheet = app.split("private struct NativePromptSheet:", 1)[1].split("\nprivate struct ", 1)[0]
    assert ".interactiveDismissDisabled(composer.sending)" not in sheet
    assert 'Button("Check Chat")' in sheet
    assert "if let request = composer.pending {" in sheet
    assert "composer.startSend()" in sheet
    close = sheet.split("connectionSheetHeader(", 1)[1].split("if composer.scope", 1)[0]
    assert 'connectionIconButton("xmark", label: "Close") { close() }' in close
    assert ".disabled(composer.sending)" not in close
    close_action = sheet.split("private func close() {", 1)[1].split("\n    }", 1)[0]
    assert "closeInline()" in close_action
    assert "dismiss()" in close_action
    assert ".cancel(" not in sheet
    probe = tmp_path / "probe"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-parse-as-library",
            "-swift-version",
            "6",
            "-target",
            f"{platform.machine()}-apple-macosx15.0",
            str(ROOT / "apple/App/NativePrompt.swift"),
            str(ROOT / "tests/fixtures/apple-prompt-lifecycle.swift"),
            "-o",
            str(probe),
        ],
        check=True,
        timeout=scaled(55),
    )
    subprocess.run([str(probe)], env={**os.environ, "PROMPT_TEST_ROOT": str(tmp_path)}, check=True, timeout=scaled(15))
