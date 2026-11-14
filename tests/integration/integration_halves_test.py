"""CI runs the integration level as two pytest sessions; together they must run every test exactly once."""

import subprocess
import sys
from pathlib import Path

import pytest

from tests import conftest

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[2]


def _collect_all() -> dict[int, list[str]]:
    """Collect halves 1, 2 and the whole level (0) side by side: three imports of the level, one wait."""
    base = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "-n", "0"]
    base += ["-m", "(integration) and not perf"]
    runs = {
        half: subprocess.Popen(
            [*base, f"--integration-half={half}"], cwd=_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        for half in (0, 1, 2)
    }
    collected = {}
    for half, process in runs.items():
        out, err = process.communicate(timeout=50)
        assert process.returncode == 0, out[-2000:] + err[-2000:]
        collected[half] = [line for line in out.splitlines() if "::" in line]
    return collected


def test_the_two_halves_cover_every_integration_file_once_and_alternate_in_sorted_order():
    files = conftest.integration_files()
    halves = {name: conftest.integration_half(name) for name in files}
    assert set(halves.values()) == {1, 2}
    assert abs(list(halves.values()).count(1) - list(halves.values()).count(2)) <= 1
    assert [halves[name] for name in files] == [index % 2 + 1 for index in range(len(files))]
    assert "integration/draft_workflow_test.py" in halves
    assert "unit/modules/user_chat_test.py" in halves  # the legacy-placed integration files are cut too


@pytest.mark.timeout(60)
def test_real_collection_of_both_halves_is_disjoint_and_equals_the_whole_level():
    collected = _collect_all()
    first, second, whole = collected[1], collected[2], collected[0]
    assert first
    assert second
    assert not set(first) & set(second)
    assert sorted(first + second) == sorted(whole)
    assert len(whole) == len(set(whole))
