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
    first_party = [p["name"] for p in found["plugins"] if p["source"] == "first-party"]
    assert first_party == [
        "heinzel-acme",
        "heinzel-atlassian",
        "heinzel-clock",
        "heinzel-github",
        "heinzel-google",
        "heinzel-tailscale",
        "heinzel-zoom",
    ]
    assert [p["name"] for p in found["plugins"][: len(first_party)]] == first_party  # listed first
    # GitHub may rate-limit unauthenticated callers; then the search degrades to a warning instead of failing.
    assert all(warning.startswith("GitHub could not be searched") for warning in found["warnings"])
    assert all(p["community"] for p in found["plugins"] if p["source"] == "github")
