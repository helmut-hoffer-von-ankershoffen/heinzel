"""Real generated browser audio reaches the production capture graph and spectrum without device capture."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e
REPO = Path(__file__).resolve().parents[2]


def test_generated_audio_drives_visible_spectrum_and_capture_controls(tmp_path):
    """Exercise actual Web Audio, canvas, silence and cleanup at desktop and phone widths."""
    shots = Path(os.environ.get("HZ_SCREENSHOTS", tmp_path / "screenshots"))
    driven = subprocess.run(
        [sys.executable, str(REPO / "scripts/web_build.py"), "exec", "node", "e2e/audio-visualizer.mjs", str(shots)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=110,
        check=False,
    )
    assert driven.returncode == 0, driven.stderr
    assert json.loads(driven.stdout.strip().splitlines()[-1]) == {
        "viewports": [1440, 390],
        "realSignal": True,
        "realSilence": True,
        "cleanup": True,
        "errors": [],
    }
