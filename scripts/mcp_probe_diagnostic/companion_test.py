"""One bounded synthetic CPU task on the otherwise idle third pytest worker."""

import hashlib
import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_cpu_companion() -> None:
    """Spend at most 3.5 seconds of wall time hashing locally and record its interval."""
    directory = Path(os.environ["VNXD_PHASE_DIR"])
    started = time.monotonic_ns()
    (directory / "companion.start").write_text(str(started))
    deadline = started + 3_500_000_000
    try:
        block = b"synthetic-companion" * 512
        while time.monotonic_ns() < deadline:
            hashlib.sha256(block).digest()
    finally:
        (directory / "companion.end").write_text(str(time.monotonic_ns()))
