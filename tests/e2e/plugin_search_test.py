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
    # Catalogs are metadata-driven and evolve independently of core releases.
    assert set(found) == {"plugins", "warnings"}
    assert all(plugin["install_spec"] and plugin["name"] for plugin in found["plugins"])
    assert all(plugin["community"] for plugin in found["plugins"] if plugin["source"] == "github")
    assert all(isinstance(warning, str) and warning for warning in found["warnings"])
