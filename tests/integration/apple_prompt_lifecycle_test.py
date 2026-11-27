"""Native prompt state survives a dismissed sheet while its answer is suspended."""

import platform
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.real_system,
    pytest.mark.integration,
    pytest.mark.macos_specific,
    pytest.mark.skipif(sys.platform != "darwin", reason="The Swift app requires macOS"),
]
ROOT = Path(__file__).resolve().parents[2]


def test_prompt_sheet_and_suspended_send_lifecycle(tmp_path):
    app = (ROOT / "apple/App/HeinzelApp.swift").read_text()
    sheet = app.split("private struct NativePromptSheet:", 1)[1].split("private struct ConnectionHelp:", 1)[0]
    assert ".interactiveDismissDisabled(composer.sending)" not in sheet
    assert 'Button("Check Chat")' in sheet
    assert "if let request = composer.pending {" in sheet
    assert "composer.startSend()" in sheet
    close = sheet.split('connectionSheetHeader("Write a Prompt")', 1)[1].split("if composer.scope", 1)[0]
    assert ".disabled(composer.sending)" not in close

    source = (ROOT / "apple/App/NativePrompt.swift").read_text(encoding="utf-8")
    composer = source.split("struct NativePromptRequest:", 1)[1].split("\nstruct WorkspaceView:", 1)[0]
    composer = "struct NativePromptRequest:" + composer
    # Keep the production persistence logic, but isolate its Application Support root for this probe.
    start = composer.index("        var directory = try FileManager.default.url(")
    end = composer.index('            .appendingPathComponent("HeinzelPromptDrafts"', start)
    composer = (
        composer[:start]
        + '        var directory = URL(fileURLWithPath: ProcessInfo.processInfo.environment["PROMPT_TEST_ROOT"]!)\n'
        + composer[end:]
    )
    fixture = (ROOT / "tests/fixtures/apple-prompt-lifecycle.swift").read_text()
    probe_source = tmp_path / "probe.swift"
    probe_source.write_text(fixture.replace("// PROMPT_COMPOSER", composer))
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
            str(probe_source),
            "-o",
            str(probe),
        ],
        check=True,
        timeout=55,
    )
    import os

    subprocess.run([str(probe)], env={**os.environ, "PROMPT_TEST_ROOT": str(tmp_path)}, check=True, timeout=15)
