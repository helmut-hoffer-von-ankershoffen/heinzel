"""The compatibility build rejects modified source before running a build backend."""

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/bootstrap-pyyaml.sh"


def test_bootstrap_uses_the_locked_pyyaml_source():
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    package = next(item for item in lock["package"] if item["name"] == "pyyaml")
    script = SCRIPT.read_text()
    assert f"source_url={package['sdist']['url']}\n" in script
    assert f"source_sha={package['sdist']['hash'].removeprefix('sha256:')}\n" in script
    assert f"pyyaml-{package['version']}.tar.gz" in script


def project_environment(tmp_path):
    """Use a real isolated standard-library interpreter without installing any dependency."""
    environment = tmp_path / "project-environment"
    (environment / "bin").mkdir(parents=True)
    (environment / "bin/python").symlink_to(sys.executable)
    (tmp_path / "uv.lock").write_bytes((ROOT / "uv.lock").read_bytes())
    return {**os.environ, "UV_PROJECT_ENVIRONMENT": str(environment)}


def test_tampered_source_never_reaches_build_backend(tmp_path):
    tools = tmp_path / "bin"
    tools.mkdir()
    marker = tmp_path / "backend-started"
    curl = tools / "curl"
    curl.write_text('#!/bin/sh\nfor last do :; done\nprintf tampered > "$last"\n')
    curl.chmod(0o755)
    uv = tools / "uv"
    uv.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 99\n')
    uv.chmod(0o755)
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        env={**project_environment(tmp_path), "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode != 0
    assert not marker.exists()


@pytest.mark.parametrize("mismatch", ["version", "url", "hash"])
def test_unreviewed_lock_cannot_download_or_start_a_backend(tmp_path, mismatch):
    environment = project_environment(tmp_path)
    package = next(
        item for item in tomllib.loads((ROOT / "uv.lock").read_text())["package"] if item["name"] == "pyyaml"
    )
    source = dict(package["sdist"])
    version = package["version"]
    if mismatch == "version":
        version = "999.0.0"
    elif mismatch == "url":
        source["url"] = "https://example.test/unreviewed-source.tar.gz"
    else:
        source["hash"] = "sha256:" + "0" * 64
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "pyyaml"\n'
        f'version = "{version}"\nsdist = {{url = "{source["url"]}", hash = "{source["hash"]}"}}\n'
    )
    tools = tmp_path / "bin"
    tools.mkdir()
    marker = tmp_path / "unreviewed-step-started"
    for name in ("curl", "uv"):
        stub = tools / name
        stub.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 99\n')
        stub.chmod(0o755)
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        env={**environment, "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode != 0
    assert "lock differs from the reviewed PyYAML bootstrap" in result.stderr
    assert not marker.exists()
