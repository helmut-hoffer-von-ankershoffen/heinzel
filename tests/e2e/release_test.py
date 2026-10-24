"""`hz system release` against the real GitHub releases API. Needs network."""

import json
import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e


def test_system_release_asks_github_for_the_latest_release():
    result = subprocess.run(
        [sys.executable, "-m", "heinzel", "system", "release", "--json"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    check = json.loads(result.stdout)
    # GitHub may rate-limit unauthenticated callers; then the check degrades to a warning instead of failing.
    if check["warnings"]:
        assert check["latest"] is None
    else:
        assert check["url"].startswith("https://github.com/")
        assert check["latest"]
