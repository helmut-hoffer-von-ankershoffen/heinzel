"""The generated documents are fresh: `generate_docs.py --check` finds nothing stale, the references included."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def test_generate_docs_check_finds_nothing_stale_including_the_references():
    env = {k: v for k, v in os.environ.items() if k != "HZ_SYSTEM_AGENT__SKILL_DIRS"}
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_docs.py"), "--check"],
        capture_output=True, text=True, cwd=ROOT, env=env, timeout=14, check=False,
    )  # fmt: skip
    # Measured: about 4 s idle and at load 12, so it fits the integration limit of 15 s.
    assert result.returncode == 0, result.stderr
    assert importlib.util.find_spec("heinzel")
