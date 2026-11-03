"""Real lane collection retains legacy fixtures and fails closed on runtime marker/path mismatches."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("invalid", [None, "wrong_lane", "unowned", "syntax"])
def test_owned_lanes_preserve_cases_fixtures_and_errors(tmp_path, full, invalid):
    tests = tmp_path / "tests"
    for level in ("unit", "integration", "e2e"):
        (tests / level).mkdir(parents=True)
    unit, integration = tests / "unit", tests / "integration"
    shutil.copyfile(Path(__file__).resolve().parents[1] / "conftest.py", tests / "conftest.py")
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\ntestpaths = tests\nmarkers =\n    unit: unit\n    integration: integration\n    e2e: e2e\n"
    )
    (unit / "conftest.py").write_text(
        "import pytest\n@pytest.fixture(autouse=True)\ndef legacy_fixture(monkeypatch):\n"
        "    monkeypatch.setenv('LEGACY_FIXTURE', 'present')\n"
    )
    (unit / "unit_test.py").write_text(
        "from pathlib import Path\nimport pytest\npytestmark = pytest.mark.unit\n"
        "Path(__file__).with_suffix('.imported').touch()\ndef test_unit(): pass\n"
    )
    (unit / "steps_signal_test.py").write_text(
        "import os\nimport pytest\npytestmark = pytest.mark.integration\n"
        "def test_legacy():\n    assert os.environ['LEGACY_FIXTURE'] == 'present'\n"
    )
    (integration / "dynamic_test.py").write_text(
        "import pytest\npytestmark = pytest.mark.unit\nif True:\n    pytestmark = pytest.mark.integration\n"
        "def test_dynamic(): pass\n"
    )
    (integration / "current_test.py").write_text(
        "import pytest\npytestmark = pytest.mark.integration\ndef test_current(): pass\n"
    )
    if invalid == "wrong_lane":
        (unit / "wrong_test.py").write_text(
            "import pytest\npytestmark = pytest.mark.integration\ndef test_wrong(): pass\n"
        )
    elif invalid == "unowned":
        (tests / "unowned_test.py").write_text(
            "import pytest\npytestmark = pytest.mark.integration\ndef test_unowned(): pass\n"
        )
    elif invalid == "syntax":
        (unit / "syntax_test.py").write_text("import pytest\npytestmark = pytest.mark.unit\nbroken = (\n")
    expression = "(unit) and not perf" if invalid in {"wrong_lane", "syntax"} else "(integration) and not perf"
    command = [sys.executable, "-m", "pytest", "-m", expression, "-n", "0" if invalid else "1", "-q", "-x"]
    if full:
        command.append("--full-level-collection")
    result = subprocess.run(
        command,
        cwd=tmp_path,
        env={"HOME": str(tmp_path), "PATH": os.defpath, "LANG": "C", "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )
    output = result.stdout + result.stderr
    if invalid:
        assert result.returncode != 0
        expected = {
            "wrong_lane": "wrong_test.py: expected unit",
            "unowned": "unowned_test.py: expected",
            "syntax": "SyntaxError",
        }
        assert expected[invalid] in output
    else:
        assert result.returncode == 0, output
        assert "3 passed" in output
        assert "INTEGRATION collected worker=gw0 tests=3" in output
    if not invalid:
        assert (unit / "unit_test.imported").exists() is full
