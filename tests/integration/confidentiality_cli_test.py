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
    assert "must be a branch, tag, commit or HEAD offset" in result.stderr
    assert not destination.exists()


def _repo_with_a_marked_commit(path):
    """A repository whose latest commit message has the marker; returns the initial commit."""

    def git(*arguments):
        return subprocess.run(
            [str(shutil.which("git")), *arguments],
            cwd=path,
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
    return previous


def _scan_messages(path, revisions):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--messages", revisions],
        cwd=path,
        env={
            **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
            "CONFIDENTIAL_TERMS": "fictional-sensitive-marker",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def test_commit_range_still_scans_messages(tmp_path):
    previous = _repo_with_a_marked_commit(tmp_path)
    result = _scan_messages(tmp_path, f"{previous}..HEAD")
    assert result.returncode == 1
    assert "confidential term #1" in result.stderr
    assert "fictional-sensitive-marker" not in result.stderr


@pytest.mark.parametrize("range_text", ["main", "HEAD~1..HEAD", "HEAD~1...HEAD", "HEAD^1..main"])
def test_every_revision_form_the_hooks_use_is_scanned(tmp_path, range_text):
    _repo_with_a_marked_commit(tmp_path)
    result = _scan_messages(tmp_path, range_text)
    assert result.returncode == 1, result.stderr
    assert "fictional-sensitive-marker" not in result.stderr


@pytest.mark.parametrize(
    "operand", ["--output=x", "-x..HEAD", "HEAD..--output=x", "$(touch x)", "no-such-branch", "HEAD~1;id"]
)
def test_anything_but_a_revision_is_refused_before_git_reads_it(tmp_path, operand):
    subprocess.run([str(shutil.which("git")), "init", "--quiet"], cwd=tmp_path, check=True, timeout=10)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--messages", operand],
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
    assert not (tmp_path / "x").exists()
