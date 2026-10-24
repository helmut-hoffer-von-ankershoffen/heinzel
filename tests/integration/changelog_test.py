"""The git-cliff configuration in pyproject.toml renders clean changelog entries from squash-merged pull requests."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
GIT_CLIFF = Path(sys.executable).parent / "git-cliff"
SQUASH_COMMIT = """refactor!: name plugins heinzel-<name> (#12)

Plugins are named `heinzel-<name>`: everything is renamed.

BREAKING CHANGE: plugin distributions are named `heinzel-<name>` instead
of `heinzel-plugin-<name>`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

---------

Co-authored-by: Someone Else <someone@example.com>
"""


def git(repo: Path, *args: str) -> None:
    env = os.environ | {
        "GIT_AUTHOR_NAME": "Dev",
        "GIT_AUTHOR_EMAIL": "dev@example.com",
        "GIT_COMMITTER_NAME": "Dev",
        "GIT_COMMITTER_EMAIL": "dev@example.com",
    }
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env, timeout=60)


def test_squash_merge_trailers_stay_out_of_the_changelog(tmp_path):
    if not GIT_CLIFF.exists():
        pytest.skip("git-cliff is not installed in this environment")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "commit", "-q", "--allow-empty", "--cleanup=verbatim", "-m", SQUASH_COMMIT)
    # git-cliff reads [tool.git-cliff] from the pyproject.toml of the working directory, as in a release.
    shutil.copy(REPO / "pyproject.toml", tmp_path / "pyproject.toml")

    result = subprocess.run(
        [str(GIT_CLIFF), "--offline", "--unreleased", "--tag", "v0.1.0"],
        cwd=tmp_path, capture_output=True, text=True, check=True, timeout=60,
    )  # fmt: skip

    bullets = [line for line in result.stdout.splitlines() if line.startswith("- ")]
    assert len(bullets) == 1, result.stdout
    assert bullets[0].endswith(
        " — **Breaking:** plugin distributions are named `heinzel-<name>` instead of `heinzel-plugin-<name>`."
    )
    for leak in ("Generated with", "---", "Co-authored-by", "someone@example.com"):
        assert leak not in result.stdout
