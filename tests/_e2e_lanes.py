"""Read the actual Makefile's E2E commands without building or running a fixture."""

import shlex
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def lane_commands(suite: str = "") -> list[list[str]]:
    """Expand the real recipe, including its README split, while leaving every dependency unexecuted."""
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "test-e2e", f"SUITE={suite}", "STEPS=e2e-lane-steps"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    [line] = [line for line in result.stdout.splitlines() if line.startswith("e2e-lane-steps ")]
    return [shlex.split(step.partition("=")[2]) for step in shlex.split(line)[1:]]
