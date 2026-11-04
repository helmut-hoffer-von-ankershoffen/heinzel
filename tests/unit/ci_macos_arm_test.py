"""macOS CI uses Apple silicon and refuses an unexpected Intel runner."""

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def runner_labels(job):
    """Inspect runner and matrix declarations, including labels embedded in fromJSON expressions."""
    return set(re.findall(r"macos-[A-Za-z0-9-]+", str((job.get("runs-on"), job.get("strategy", {}).get("matrix")))))


def test_all_core_and_generated_macos_runner_labels_are_arm_only():
    paths = [*(ROOT / ".github/workflows").glob("*.yml"), *(ROOT / "template/.github/workflows").glob("*.yml.jinja")]
    found = set()
    for path in paths:
        for job in yaml.safe_load(path.read_text()).get("jobs", {}).values():
            labels = runner_labels(job)
            assert labels <= {"macos-latest"}, (path.name, labels)
            found.update(labels)
    assert found == {"macos-latest"}


@pytest.mark.parametrize("label", ["macos-15-intel", "macos-26-intel", "macos-latest-large"])
def test_intel_runner_labels_are_outside_the_approved_set(label):
    assert not runner_labels({"runs-on": label}) <= {"macos-latest"}


@pytest.mark.parametrize(
    ("runner_os", "architecture", "succeeds"),
    [("macOS", "arm64", True), ("macOS", "x86_64", False), ("Linux", "x86_64", True)],
)
def test_shared_bootstrap_checks_actual_macos_architecture(tmp_path, runner_os, architecture, succeeds):
    action_path = ROOT / ".github/actions/setup-uv"
    script = yaml.safe_load((action_path / "action.yml").read_text())["runs"]["steps"][0]["run"]
    binary = tmp_path / "uname"
    binary.write_text(f"#!/bin/sh\nprintf '%s\n' '{architecture}'\n")
    binary.chmod(0o700)
    output = tmp_path / "output"
    result = subprocess.run(
        ["bash", "-e", "-c", script],
        env={
            "PATH": f"{tmp_path}{os.pathsep}/usr/bin:/bin",
            "RUNNER_OS": runner_os,
            "ACTION_PATH": str(action_path),
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert (result.returncode == 0) is succeeds
    assert output.exists() is succeeds


def test_electron_checks_actual_macos_architecture_without_uv_bootstrap():
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    [guard] = [step for step in jobs["electron"]["steps"] if step.get("name") == "Require Apple silicon for macOS CI"]
    assert guard["if"] == "runner.os == 'macOS'"
    assert guard["run"] == "make ci-macos-arm"
    steps = jobs["electron"]["steps"]
    checkout = next(index for index, step in enumerate(steps) if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout < steps.index(guard)


@pytest.mark.parametrize(("architecture", "succeeds"), [("arm64", True), ("x86_64", False)])
def test_native_architecture_target_refuses_intel(tmp_path, architecture, succeeds):
    binary = tmp_path / "uname"
    binary.write_text(f"#!/bin/sh\nprintf '%s\n' '{architecture}'\n")
    binary.chmod(0o700)
    result = subprocess.run(
        ["/usr/bin/make", "--no-print-directory", "ci-macos-arm"],
        cwd=ROOT,
        env={"PATH": f"{tmp_path}{os.pathsep}/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert (result.returncode == 0) is succeeds
