"""One bounded hosted diagnostic of the installer coverage selection."""

import json
import os
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.timeout(180)]


@pytest.mark.filterwarnings("ignore:record_property is incompatible:pytest.PytestWarning")
def test_record_installer_coverage_opportunities(tmp_path, record_property):
    """Keep the Linux raw branch report in the JUnit artifact for diagnosis."""
    env = dict(os.environ, COVERAGE_FILE=str(tmp_path / ".coverage"))
    selection = [
        "tests/unit/heads/desktop_release_progress_test.py",
        "tests/unit/heads/desktop_release_trust_test.py",
        "tests/unit/heads/desktop_recovery_safety_test.py",
        "tests/unit/heads/desktop_location_test.py",
        "tests/unit/heads/desktop_seal_test.py",
        "tests/unit/heads/desktop_app_golden_test.py",
    ]
    subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "--branch",
            "--source=heinzel.heads._desktop_release,heinzel.heads._desktop_installation",
            "-m",
            "pytest",
            "-q",
            *selection,
        ],
        env=env,
        check=True,
        timeout=150,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [sys.executable, "-m", "coverage", "combine", str(tmp_path)],
        env=env,
        check=True,
        timeout=20,
        capture_output=True,
        text=True,
    )
    report = tmp_path / "coverage.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "json",
            "--include=*/_desktop_release.py,*/_desktop_installation.py",
            "--fail-under=0",
            "-o",
            str(report),
        ],
        env=env,
        check=True,
        timeout=20,
        capture_output=True,
        text=True,
    )
    data = json.loads(report.read_text())
    assert data["meta"]["branch_coverage"]
    assert len(data["files"]) == 2
    record_property("installer_coverage_json", json.dumps(data, separators=(",", ":")))
