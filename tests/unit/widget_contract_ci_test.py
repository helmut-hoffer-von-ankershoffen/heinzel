"""The focused native contract receipt must not turn skips into a pass."""

import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/widget_contract_ci.sh"
NAMES = (
    "test_cancelled_issue_race_replans_and_replays_revoke",
    "test_native_fenced_retry_after_lost_core_reply",
    "test_native_script_and_core_recover_lost_reply",
    "test_real_webkit_cancelled_lost_issue_reply",
)


@pytest.mark.parametrize("defect", [None, "skipped", "failure", "error", "missing", "duplicate"])
def test_widget_contract_report_requires_all_four_clean_cases(tmp_path: Path, defect: str | None) -> None:
    suite = ET.Element("testsuite")
    names = NAMES[:-1] if defect == "missing" else (*NAMES, NAMES[0]) if defect == "duplicate" else NAMES
    for index, name in enumerate(names):
        case = ET.SubElement(suite, "testcase", name=name)
        if index == 0 and defect in {"skipped", "failure", "error"}:
            ET.SubElement(case, defect)
    report = tmp_path / "focused-artifacts" / "junit.xml"
    report.parent.mkdir()
    ET.ElementTree(suite).write(report)
    result = subprocess.run(["bash", str(SCRIPT), "verify"], cwd=tmp_path, capture_output=True, check=False)
    assert (result.returncode == 0) is (defect is None)
