"""Exercise the actual controller-to-worker E2E scheduling hint handoff."""

import json
import os
import sys
from pathlib import Path

import pytest

from tests.e2e.docs_test import GuardedCommand

pytestmark = pytest.mark.integration


def _run(tmp_path: Path, workers: int) -> list[list[str]]:
    root = Path(__file__).resolve().parents[2]
    case = tmp_path / f"case-{workers}"
    tests = case / "tests"
    tests.mkdir(parents=True)
    conftest = (root / "tests" / "conftest.py").read_text()
    (tests / "conftest.py").write_text(
        conftest
        + "\n\ndef pytest_collection_finish(session):\n"
        + "    import json, os\n"
        + "    from pathlib import Path\n"
        + '    worker = os.environ.get("PYTEST_XDIST_WORKER", "controller")\n'
        + '    path = Path(os.environ["HZ_ORDER_DIR"], worker + ".json")\n'
        + "    path.write_text(json.dumps([i.nodeid for i in session.items]))\n"
    )
    (case / "pytest.ini").write_text("[pytest]\nmarkers =\n    e2e: isolated test\n    e2e_early: ordering hint\n")
    (tests / "test_items.py").write_text(
        "import pytest\npytestmark = pytest.mark.e2e\n"
        "@pytest.mark.parametrize('n', range(12))\ndef test_regular(n): pass\n"
        "@pytest.mark.e2e_early\n@pytest.mark.parametrize('n', range(3))\ndef test_early(n): pass\n"
    )
    env = {
        "HOME": str(case),
        "TMPDIR": str(case),
        "PATH": os.defpath,
        "LANG": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HZ_ORDER_DIR": str(case),
    }
    guard = GuardedCommand(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-m", "e2e", "-n", str(workers), "-q", "tests"],
        env=env,
        cwd=case,
    )
    try:
        status = guard.wait(10)
    finally:
        guard.stop()
    assert status == 0
    assert "15 passed" in guard.capture.text("stdout")
    return [json.loads(path.read_text()) for path in sorted(case.glob("gw*.json"))]


def _expected() -> list[str]:
    return [f"tests/test_items.py::test_regular[{index}]" for index in range(12)] + [
        f"tests/test_items.py::test_early[{index}]" for index in range(3)
    ]


def test_hint_is_shared_by_all_three_workers(tmp_path: Path) -> None:
    scheduled = _run(tmp_path, 3)
    expected = _expected()
    assert len(scheduled) == 3
    ordered = [expected[12], expected[0], expected[13], expected[1], expected[14], expected[2], *expected[3:12]]
    assert all(items == ordered for items in scheduled)


def test_unknown_worker_count_keeps_order(tmp_path: Path) -> None:
    fallback = _run(tmp_path, 2)
    assert len(fallback) == 2
    assert all(items == _expected() for items in fallback)
