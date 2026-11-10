"""Capture-free hosted preflight for a future native system-audio study."""

import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e
REPO = Path(__file__).resolve().parents[2]


def test_native_system_audio_capture_free_preflight():
    """Only a disposable macOS ARM host may launch the isolated native fixture."""
    if (
        os.environ.get("CI") != "true"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or os.environ.get("FOCUSED_SELECTOR")
        != "tests/e2e/native_system_audio_test.py::test_native_system_audio_capture_free_preflight"
        or platform.system() != "Darwin"
    ):
        pytest.skip("requires explicitly selected disposable macOS hosted CI")
    assert platform.machine() == "arm64"
    setup = subprocess.run(
        [sys.executable, "scripts/web_build.py", "pnpm", "--dir", "../desktop", "install", "--frozen-lockfile"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert setup.returncode == 0, setup.stderr
    environment = {**os.environ, "HZ_DISPOSABLE_NATIVE_CI": "1"}
    result = subprocess.run(
        [sys.executable, "scripts/web_build.py", "exec", "node", "../desktop/e2e/system-audio-preflight.mjs"],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "no media capture requested" in result.stdout
    assert "isolated profile removed" in result.stdout
