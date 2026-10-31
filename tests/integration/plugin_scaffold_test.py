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
    assert arguments[:8] == [
        "run",
        "--quiet",
        "--no-project",
        "--no-build",
        "--python",
        "3.14",
        "python",
        "scripts/plugin_tooling.py",
    ]
    assert arguments[8] == "--toolkit"
    assert arguments[9].startswith("heinzel-plugin-tools @ https://github.com/")
    assert "#sha256=" in arguments[9]
    assert arguments[10:] == ["hz-plugin", "setup"]


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


def test_fresh_governed_setup_creates_its_lock_before_frozen_install(tmp_path):
    """The rendered Makefile owns initial resolution; an author never needs a preliminary uv lock."""
    import json
    import tomllib

    from copier import run_copy

    repository = Path(__file__).resolve().parents[2]
    source = tmp_path / "template-source"
    source.mkdir()
    shutil.copy(repository / "copier.yml", source / "copier.yml")
    shutil.copytree(repository / "template", source / "template")
    destination = tmp_path / "governed"
    run_copy(
        str(source),
        destination,
        data={"preset": "governed", "plugin_name": "harbor"},
        defaults=True,
        quiet=True,
        vcs_ref="HEAD",
    )
    assert not (destination / "uv.lock").exists()
    tools = tmp_path / "bin"
    tools.mkdir()
    trace = tmp_path / "commands.jsonl"
    uv = tools / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys,tomllib\nfrom pathlib import Path\n"
        "args=sys.argv[1:]\n"
        "with Path(os.environ['TRACE']).open('a') as out: out.write(json.dumps(args)+'\\n')\n"
        "project=tomllib.loads(Path('pyproject.toml').read_text())['project']\n"
        "lock=Path('uv.lock')\n"
        "if args[0]=='lock':\n"
        "    assert not lock.exists(), 'setup must preserve an existing lock'\n"
        "    lock.write_text('version = 1\\nrevision = 3\\n[[package]]\\nname = '+json.dumps(project['name'])"
        "+'\\nversion = '+json.dumps(project['version'])+'\\nsource = { editable = '+json.dumps('.')+' }\\n')\n"
        "elif args[0]=='sync':\n"
        "    assert '--frozen' in args, 'installation must enforce the resolved lock'\n"
        "    record=tomllib.loads(lock.read_text())['package'][0]\n"
        "    assert (record['name'],record['version'])==(project['name'],project['version'])\n"
    )
    uv.chmod(0o755)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GIT_", "UV_")) and key not in {"MAKEFLAGS", "MAKELEVEL", "MFLAGS", "VIRTUAL_ENV"}
    }
    environment.update(PATH=f"{tools}{os.pathsep}{os.environ['PATH']}", TRACE=str(trace))
    for _ in range(2):
        result = subprocess.run(
            [str(shutil.which("make")), "setup"],
            cwd=destination,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
    commands = [json.loads(line) for line in trace.read_text().splitlines()]
    assert commands[0] == ["lock", "--quiet"]
    assert sum(command[0] == "lock" for command in commands) == 1
    assert sum(command[0] == "sync" for command in commands) == 2
    assert tomllib.loads((destination / "uv.lock").read_text())["package"][0]["name"] == "heinzel-harbor"
