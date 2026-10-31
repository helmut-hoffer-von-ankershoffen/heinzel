"""Revision scanning cannot turn an operand into a Git option."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
SCRIPT = Path(__file__).resolve().parents[2] / "scripts/check_confidentiality.py"


def test_revision_option_cannot_write_a_file(tmp_path):
    destination = tmp_path / "injected-output"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), f"--messages=--output={destination}"],
        cwd=tmp_path,
        env={
            **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
            "CONFIDENTIAL_TERMS": "fictional-sensitive-marker",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 2
    assert "not a Git option" in result.stderr
    assert not destination.exists()


def test_commit_range_still_scans_messages(tmp_path):
    def git(*arguments):
        return subprocess.run(
            [str(shutil.which("git")), *arguments],
            cwd=tmp_path,
            env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()

    git("init", "--quiet", "--initial-branch=main")
    git("config", "user.name", "Example Author")
    git("config", "user.email", "author@example.test")
    git("-c", "commit.gpgsign=false", "commit", "--quiet", "--allow-empty", "-m", "Initial")
    previous = git("rev-parse", "HEAD")
    git("-c", "commit.gpgsign=false", "commit", "--quiet", "--allow-empty", "-m", "Contains fictional-sensitive-marker")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--messages", f"{previous}..HEAD"],
        cwd=tmp_path,
        env={
            **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
            "CONFIDENTIAL_TERMS": "fictional-sensitive-marker",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 1
    assert "confidential term #1" in result.stderr
    assert "fictional-sensitive-marker" not in result.stderr
