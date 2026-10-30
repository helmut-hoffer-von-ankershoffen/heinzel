"""The actual standalone starter's Makefile forwards options as data, never Make expressions."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_starter_make_bootstraps_its_pinned_toolkit_without_overrides(tmp_path):
    make = shutil.which("make")
    assert make
    source = next((Path(__file__).parents[2] / "starter").glob("*Makefile*.jinja"))
    stub = tmp_path / "uv"
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    stub.chmod(0o755)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"TOOLING", "CORE", "TAG", "REGISTRY", "MAKEFLAGS", "MAKELEVEL", "MFLAGS"}
    }
    environment["PATH"] = str(tmp_path) + os.pathsep + os.environ["PATH"]
    result = subprocess.run(
        [make, "-f", str(source), "setup"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    arguments = result.stdout.splitlines()
    assert arguments[:4] == ["run", "--quiet", "--no-project", "--with"]
    assert arguments[4].startswith("heinzel-plugin-tools @ https://github.com/")
    assert "#sha256=" in arguments[4]
    assert arguments[5:] == ["hz-plugin", "setup"]


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


@pytest.mark.parametrize("frozen_caller", [False, True])
def test_dependency_validator_refuses_stale_metadata_without_rewriting_its_lock(tmp_path, monkeypatch, frozen_caller):
    uv = shutil.which("uv")
    assert uv
    if frozen_caller:
        monkeypatch.setenv("UV_FROZEN", "1")
        monkeypatch.setenv("UV_NO_SYNC", "1")
    else:
        monkeypatch.delenv("UV_FROZEN", raising=False)
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("UV_") and key != "VIRTUAL_ENV"
    }
    environment["UV_PYTHON"] = sys.executable
    project = tmp_path / "pyproject.toml"
    source = '[project]\nname="harbor-fixture"\nversion="0.1.0"\nrequires-python=">=3.12"\n'
    project.write_text(source)
    # This new, synthetic project must create its own lock; the repository stays frozen in CI.
    created = subprocess.run(
        [uv, "lock", "--offline"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert created.returncode == 0, created.stderr
    lock = (tmp_path / "uv.lock").read_bytes()
    if frozen_caller:
        environment["UV_FROZEN"] = "1"  # --check must enforce freshness even under the production CI flag.
    valid = subprocess.run(
        [uv, "lock", "--check", "--offline"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        timeout=15,
    )
    assert valid.returncode == 0, valid.stderr
    project.write_text(source.replace("0.1.0", "0.2.0"))
    result = subprocess.run(
        [uv, "lock", "--check", "--offline"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert (tmp_path / "uv.lock").read_bytes() == lock
