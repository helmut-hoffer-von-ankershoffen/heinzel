"""The compatibility build rejects modified source before running a build backend."""

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/bootstrap-pyyaml.sh"
MODULE = ROOT / "src/heinzel/bootstrap_pyyaml.py"


def test_bootstrap_uses_the_locked_pyyaml_source():
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    package = next(item for item in lock["package"] if item["name"] == "pyyaml")
    pins = json.loads(
        subprocess.run(
            [sys.executable, "-I", "-S", str(MODULE), "pins"], capture_output=True, text=True, check=True, timeout=10
        ).stdout
    )
    assert pins["source_url"] == package["sdist"]["url"]
    assert "sha256:" + pins["source_sha256"] == package["sdist"]["hash"]
    assert pins["version"] == package["version"]


def test_the_script_and_the_template_keep_no_copy_of_a_pin():
    """One source: the module the Core wheel ships. The script and a generated project only run it."""
    script = SCRIPT.read_text()
    assert "files.pythonhosted.org" not in script
    assert "sha256" not in script
    assert "6.0.3" not in script
    assert not (ROOT / "scripts/pyyaml-build-requirements.txt").exists()
    mirror = ROOT / "template/scripts/bootstrap_pyyaml.py.jinja"
    assert mirror.is_symlink()
    assert mirror.resolve() == MODULE
    # copier renders the mirror as a template: nothing in it may read as Jinja
    assert not any(mark in MODULE.read_text() for mark in ("{{", "{%", "{#"))


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
