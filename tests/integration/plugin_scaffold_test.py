"""The actual standalone starter's Makefile forwards options as data, never Make expressions."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("option", ["TOOLING", "CORE", "TAG", "REGISTRY"])
def test_standalone_make_forwards_values_without_evaluating_them(tmp_path, option):
    make = shutil.which("make")
    assert make
    source = next((Path(__file__).parents[2] / "starter").glob("*Makefile*.jinja"))
    stub = tmp_path / "uv"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    sentinel = tmp_path / "must-not-exist"
    result = subprocess.run(
        [make, "-f", str(source), "check", f"{option}=$(shell touch {sentinel})"],
        cwd=tmp_path,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert not sentinel.exists()


def test_dependency_validator_refuses_stale_metadata_without_rewriting_its_lock(tmp_path):
    uv = shutil.which("uv")
    assert uv
    project = tmp_path / "pyproject.toml"
    source = '[project]\nname="harbor-fixture"\nversion="0.1.0"\nrequires-python=">=3.12"\n'
    project.write_text(source)
    # This new, synthetic project must create its own lock; the repository stays frozen in CI.
    fixture_env = os.environ.copy()
    fixture_env.pop("UV_FROZEN", None)
    created = subprocess.run(
        [uv, "lock", "--offline"],
        cwd=tmp_path,
        env=fixture_env,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert created.returncode == 0, created.stderr
    lock = (tmp_path / "uv.lock").read_bytes()
    project.write_text(source.replace("0.1.0", "0.2.0"))
    result = subprocess.run(
        [uv, "lock", "--check", "--offline"],
        cwd=tmp_path,
        env=fixture_env,
        check=False,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert (tmp_path / "uv.lock").read_bytes() == lock
