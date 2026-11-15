"""The generated documents are fresh: `generate_docs.py --check` finds nothing stale, the references included."""

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
        capture_output=True, text=True, cwd=ROOT, env=env, timeout=120, check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
