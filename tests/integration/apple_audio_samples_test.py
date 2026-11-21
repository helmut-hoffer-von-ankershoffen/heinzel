"""Exercise the Mac tap's PCM buffer without starting a capture or opening an app."""

import platform
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.integration, pytest.mark.macos_specific]


def test_system_audio_permission_description_exists():
    with (ROOT / "apple/App/Info.plist").open("rb") as source:
        assert plistlib.load(source)["NSAudioCaptureUsageDescription"].strip()


def test_background_capture_is_ios_app_only():
    with (ROOT / "apple/App/iOS-Info.plist").open("rb") as source:
        info = plistlib.load(source)
    assert info["UIBackgroundModes"] == ["screen-capture"]
    assert info["NSScreenCaptureUsageDescription"].strip()
    for target in ("App/Info.plist", "Widgets/Info.plist", "Widgets/iOS-Info.plist"):
        with (ROOT / "apple" / target).open("rb") as source:
            assert "UIBackgroundModes" not in plistlib.load(source)


@pytest.mark.skipif(sys.platform != "darwin", reason="Core Audio requires macOS")
@pytest.mark.timeout(75)
def test_tap_samples_deliver_and_bound_pcm(tmp_path):
    probe = tmp_path / "audio-samples"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-swift-version",
            "6",
            "-target",
            f"{platform.machine()}-apple-macosx15.0",
            str(ROOT / "apple/App/SystemAudio.swift"),
            str(ROOT / "tests/fixtures/apple-audio-samples.swift"),
            "-o",
            str(probe),
        ],
        check=True,
        timeout=60,
    )
    subprocess.run([str(probe)], check=True, timeout=10)
