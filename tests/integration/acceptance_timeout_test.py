"""Real collection keeps the batched acceptance-row expressions under the normal unit deadline."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROW_NODE = "tests/unit/acceptance_test.py::test_a_row_check_reads_only_the_installed_version_in_its_own_header"


def test_the_batched_acceptance_rows_and_adjacent_tests_keep_the_five_second_deadline(tmp_path):
    report = tmp_path / "timeouts.json"
    probe = """
import json
import sys
from pathlib import Path
import pytest

class Capture:
    def pytest_collection_finish(self, session):
        Path(sys.argv[1]).write_text(json.dumps({
            item.nodeid: {
                "markers": [list(marker.args) for marker in item.iter_markers("timeout")],
                "effective": list(item.get_closest_marker("timeout").args),
            }
            for item in session.items
        }))

raise SystemExit(pytest.main(
    ["--collect-only", "-q", "tests/unit/acceptance_test.py"], plugins=[Capture()]
))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, str(report)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    collected = json.loads(report.read_text())
    row_cases = {node: marker for node, marker in collected.items() if node.split("[", 1)[0] == ROW_NODE}
    adjacent = {node: marker for node, marker in collected.items() if node not in row_cases}
    assert len(row_cases) == 1
    assert adjacent
    assert all(marker == {"markers": [[5]], "effective": [5]} for marker in row_cases.values())
    assert all(marker == {"markers": [[5]], "effective": [5]} for marker in adjacent.values())
