"""Isolated build preparation keeps cache policy within its own subprocesses."""

import importlib.util
import os
import subprocess
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("isolated_build", ROOT / "scripts/build.py")
assert SPEC is not None
assert SPEC.loader is not None
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def test_backend_preparation_does_not_change_the_callers_environment(monkeypatch, tmp_path):
    controller_path = str(tmp_path / "controller")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", controller_path)
    before = dict(os.environ)
    calls = []
    monkeypatch.setattr(build, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    build.main(["sync"])

    assert dict(os.environ) == before
    export, venv, backend, controller, editable = calls
    assert export[0][:5] == ("export", "--frozen", "--only-group", "build", "--no-emit-project")
    assert "--no-build" in backend[0]
    assert venv[0][0] == "venv"
    assert "--require-hashes" in backend[0]
    assert "--requirements" in backend[0]
    assert backend[1] == {}
    assert controller == (
        ("sync", "--frozen", "--all-groups", "--no-install-project"),
        {"timeout": 600},
    )
    assert editable[0][:8] == (
        "pip",
        "install",
        "--python",
        str(Path(controller_path) / "bin/python"),
        "--no-deps",
        "--editable",
        ".",
        "--offline",
    )
    assert "--require-build-hashes" in editable[0]
    assert "--no-build-isolation" not in editable[0]
    assert editable[1] == {}
    constraints = editable[0][editable[0].index("--build-constraints") + 1]
    assert not Path(constraints).exists()


def test_missing_prefetch_artifact_stops_before_editable_build(monkeypatch):
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        if "--requirements" in args and args[:2] == ("pip", "install"):
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(build, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        build.main(["sync"])
    assert len(calls) == 3


def test_builds_require_hashes_and_offline_cache_without_exporting_options(monkeypatch):
    before = dict(os.environ)
    calls = []
    monkeypatch.setattr(build, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    build.main(["--wheel", "--out-dir", "dist"])
    assert dict(os.environ) == before
    args, kwargs = calls[-1]
    assert args[0] == "build"
    assert "--offline" in args
    assert "--require-build-hashes" in args
    assert args[-3:] == ("--wheel", "--out-dir", "dist")
    assert kwargs == {}


def test_build_group_contains_backend_extras_without_changing_runtime_requirements():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    group = project["dependency-groups"]["build"]
    assert project["build-system"]["requires"][0] in group
    assert "editables>=0.5" in group
    assert "nodejs-wheel-binaries==24.19.0" in group
    assert not any(requirement in project["project"]["dependencies"] for requirement in group)


def test_backend_only_preparation_leaves_the_running_controller_installed(monkeypatch):
    calls = []
    monkeypatch.setattr(build, "run", lambda *args, **kwargs: calls.append(args))
    build.main(["prepare"])
    assert len(calls) == 3
    assert calls[-1][:2] == ("pip", "install")
    assert "--require-hashes" in calls[-1]
    assert all("--editable" not in args and args[0] != "sync" for args in calls)


@pytest.mark.parametrize("action", [["sync"], ["prepare"], ["--wheel", "--out-dir", "dist"]])
def test_only_controller_dependency_sync_has_the_setup_job_budget(monkeypatch, action):
    calls = []
    monkeypatch.setattr(build.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    build.main(action)

    assert build.CONTROLLER_SYNC_TIMEOUT == 600
    for (command,), options in calls:
        controller_sync = command[1:] == ["sync", "--frozen", "--all-groups", "--no-install-project"]
        assert options["timeout"] == (600 if controller_sync else 300)
        assert options["check"] is True
    assert sum(options["timeout"] == 600 for _, options in calls) == (action == ["sync"])


def test_controller_dependency_sync_failure_stops_before_editable_build(monkeypatch):
    calls = []

    def run(command, **options):
        calls.append(command)
        if command[1] == "sync":
            raise subprocess.TimeoutExpired(command, options["timeout"])

    monkeypatch.setattr(build.subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired) as error:
        build.main(["sync"])
    assert error.value.timeout == 600
    assert len(calls) == 4
    assert all("--editable" not in command for command in calls)
