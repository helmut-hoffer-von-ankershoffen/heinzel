"""Real generated browser audio reaches the production capture graph and spectrum without device capture."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e
REPO = Path(__file__).resolve().parents[2]


def test_generated_audio_drives_visible_spectrum_and_capture_controls(tmp_path):
    """Exercise actual Web Audio, canvas, silence and cleanup at desktop and phone widths."""
    shots = Path(os.environ.get("HZ_SCREENSHOTS", tmp_path / "screenshots"))
    # Linux Chrome creates a Unix socket under TMPDIR; pytest's long test name can exceed its path limit.
    # TemporaryDirectory creates an owned 0700 profile and removes it on success, failure or timeout.
    # Use pytest's owned path on macOS; /tmp resolves to /private/tmp there and is not used on the Mini.
    parent = "/tmp" if sys.platform.startswith("linux") else tmp_path  # noqa: S108 - TemporaryDirectory is private
    with tempfile.TemporaryDirectory(prefix="hz-audio-", dir=parent) as directory:
        home = Path(directory)
        temp = home / "tmp"
        temp.mkdir()
        environment = {
            key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "PLAYWRIGHT_BROWSERS_PATH") if key in os.environ
        }
        environment.update(
            {
                "HOME": str(home),
                "TMPDIR": str(temp),
                "XDG_CONFIG_HOME": str(home / "config"),
                "XDG_CACHE_HOME": str(home / "cache"),
                "XDG_DATA_HOME": str(home / "data"),
            }
        )
        driven = subprocess.run(
            [
                sys.executable,
                str(REPO / "scripts/web_build.py"),
                "exec",
                "node",
                "e2e/audio-visualizer.mjs",
                str(shots),
            ],
            cwd=REPO,
            env=environment,
            capture_output=True,
            text=True,
            timeout=110,
            check=False,
        )
    assert driven.returncode == 0, driven.stderr
    result = json.loads(driven.stdout.strip().splitlines()[-1])
    # The measured tone positions (spectrum centroid, rail mass, aurora pixels), named in any failure below.
    tones = result.pop("tones")
    assert [tone["width"] for tone in tones] == [1440, 390], tones
    assert result == {
        "viewports": [1440, 390],
        "realSignal": True,
        "gpuReset": True,
        "realSilence": True,
        "cleanup": True,
        "errors": [],
    }, tones
