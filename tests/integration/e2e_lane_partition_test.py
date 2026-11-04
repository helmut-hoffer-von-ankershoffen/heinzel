"""Real collected E2E node IDs form the same complete disjoint union after CI allocation changes."""

import json
import subprocess
import sys
from collections import Counter

import pytest

from tests._e2e_lanes import REPO, lane_commands

pytestmark = pytest.mark.integration


def collect(groups: dict[str, list[list[str]]]) -> dict[str, Counter[str]]:
    # All sessions share only the read-only collection interpreter, avoiding repeated interpreter bootstrap cost.
    # Each pytest.main has its own config/session and uses the actual expanded Makefile selection unchanged.
    code = """
import json, pytest, sys
class Collected:
    def pytest_collection_finish(self, session):
        self.nodes = [item.nodeid for item in session.items]
for label, commands in json.loads(sys.argv[1]).items():
    nodes = []
    for command in commands:
        plugin = Collected()
        selection = command[command.index('-x') + 1:]
        result = pytest.main(['-m', 'e2e', '--collect-only', '-q', *selection], plugins=[plugin])
        if result:
            raise SystemExit(result)
        nodes.extend(plugin.nodes)
    print('COLLECTED_GROUP=' + json.dumps([label, nodes]))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, json.dumps(groups)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {
        label: Counter(nodes)
        for line in result.stdout.splitlines()
        if line.startswith("COLLECTED_GROUP=")
        for label, nodes in [json.loads(line.partition("=")[2])]
    }


def test_ci_lanes_preserve_every_original_node_once_including_supervised_parameterizations():
    groups = {suite: lane_commands(suite) for suite in ("", "cli", "supervised", "web-1", "web-2")}
    # Before the split, CLI ignored only browser files. Reconstruct that exact original selection.
    groups["original-cli"] = [[arg for arg in groups["cli"][-1] if arg != "--ignore=tests/e2e/ui_service_test.py"]]
    lanes = collect(groups)
    assert set(lanes) == set(groups)
    default = lanes.pop("")
    old_cli = lanes.pop("original-cli")
    assert all(count == 1 for count in default.values())
    old_cli.update({"tests/e2e/docs_test.py::test_readme_instructions": 1})
    assert lanes["cli"] + lanes["supervised"] == old_cli
    actual = sum(lanes.values(), Counter())
    assert actual == default
    assert all(count == 1 for count in actual.values())
    expected_supervised = {
        f"tests/e2e/ui_service_test.py::{name}[{platform}{suffix}]"
        for platform in ("linux", "darwin")
        for name, suffix in [
            ("test_installed_server_keeps_session_on_update_exec_and_pairing_after_cold_restart", ""),
            *[("test_fresh_installed_server_repeatedly_publishes_authenticated_control", f"-{i}") for i in range(5)],
        ]
    }
    assert set(lanes["supervised"]) == expected_supervised
    assert not set(lanes["supervised"]).intersection(lanes["cli"])
    assert "tests/e2e/tool_install_test.py::test_a_plugin_from_a_git_repository_is_added_with_confirm" in lanes["cli"]
