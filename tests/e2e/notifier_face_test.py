"""Render the production native face without posting, requesting permission or starting a service.

`make notifier` (scripts/notifier.py) compiles the proof, tests/fixtures/notifier/render_proof.swift after the
production helpers of main.swift, into build/notifier-render-proof with the digest of that exact source. The timed test
only checks the digest and runs it: compiling here overran the budget on a loaded macOS runner (bead 4mgv).
"""

import hashlib
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.macos_specific
@pytest.mark.skipif(sys.platform != "darwin", reason="AppKit rendering is macOS-only")
@pytest.mark.timeout(15)
def test_bundled_face_renders_in_an_owned_private_directory_without_posting(tmp_path):
    notifier = runpy.run_path(str(ROOT / "scripts" / "notifier.py"), run_name="notifier_build")
    proof = notifier["PROOF"]
    stamp = proof / "source.sha256"
    if not stamp.is_file():
        if os.environ.get("CI"):
            pytest.fail("make notifier did not build the face-rendering proof")
        pytest.skip("the face-rendering proof isn't built: make notifier")
    expected = hashlib.sha256(notifier["proof_source"]().encode()).hexdigest()
    assert stamp.read_text().strip() == expected, "the proof is older than main.swift or its harness: make notifier"
    rendered = subprocess.run(
        [str(proof / "RenderingProof.app" / "Contents" / "MacOS" / "render"), str(tmp_path / "blue-face.png")],
        check=True,
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert rendered.stdout.strip() == ("rendered bundled blue face; private directory removed; no notification posted")
