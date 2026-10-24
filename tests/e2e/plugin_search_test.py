"""`hz system plugin search` against the real GitHub API. Needs network."""

import json
import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e


def test_plugin_search_queries_github():
    result = subprocess.run(
        [sys.executable, "-m", "heinzel", "system", "plugin", "search", "--json"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    found = json.loads(result.stdout)
    assert found["plugins"][0]["name"] == "heinzel-clock"
    # GitHub may rate-limit unauthenticated callers; then the search degrades to a warning instead of failing.
    assert all(warning.startswith("GitHub could not be searched") for warning in found["warnings"])
    assert all(p["community"] for p in found["plugins"] if p["source"] == "github")
