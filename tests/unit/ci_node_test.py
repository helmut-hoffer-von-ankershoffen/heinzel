"""Python unit lanes select the locked wheel without touching Node executable bytes."""

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def selector(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("ci_node", ROOT / "scripts/ci_node.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prefix = tmp_path / "venv"
    site = prefix / "lib/site-packages"
    binary = site / module.NODE_RECORD
    binary.parent.mkdir(parents=True)
    marker = tmp_path / "node-started"
    binary.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 91\n")
    binary.chmod(0o111)  # Selection needs only executable metadata, not readable bytes.
    output = tmp_path / "github-path"
    output.write_text("prior-entry\n")
    distribution = SimpleNamespace(
        version="24.19.0", files=[Path(module.NODE_RECORD)], locate_file=lambda name: site / str(name)
    )
    monkeypatch.setattr(module.importlib.metadata, "distribution", lambda name: distribution)
    monkeypatch.setattr(module.build, "node_bin", lambda: binary.parent)
    monkeypatch.setattr(module.sys, "prefix", str(prefix))
    monkeypatch.setenv("GITHUB_PATH", str(output))
    original_open = Path.open

    def open_without_node_bytes(path, *args, **kwargs):
        if path == binary:
            pytest.fail("Selector must not read or hash the Node executable")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_without_node_bytes)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Selector must not execute Node or any child"))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("Selector must not execute Node or any child"))
    return module, distribution, binary, output, marker


def test_selection_exports_only_the_owned_wheel_bin_and_logs_unobserved_runtime(selector, capsys):
    module, _distribution, binary, output, marker = selector
    module.main()
    assert output.read_text() == f"prior-entry\n{binary.parent}\n"
    identity = json.loads(capsys.readouterr().out.removeprefix("node_identity "))
    assert identity == {
        "phase": "python-tests-selected",
        "runtime_version": "unobserved",
        "locked_distribution_version": "24.19.0",
        "executable": str(binary),
    }
    assert not marker.exists()


@pytest.mark.parametrize(
    "problem",
    [
        "wrong-version",
        "missing",
        "record-missing",
        "outside",
        "different-record",
        "metadata-outside",
        "not-executable",
        "writable",
        "foreign-owner",
    ],
)
def test_invalid_wheel_never_exports_a_path_or_executes_node(selector, monkeypatch, tmp_path, problem):
    module, distribution, binary, output, marker = selector
    if problem == "wrong-version":
        distribution.version = "22.23.4"
    elif problem == "missing":
        binary.unlink()
    elif problem == "record-missing":
        distribution.files = []
    elif problem == "outside":
        outside = tmp_path / "foreign/bin/node"
        outside.parent.mkdir(parents=True)
        outside.write_text("not executed")
        outside.chmod(0o111)
        monkeypatch.setattr(module.build, "node_bin", lambda: outside.parent)
        distribution.locate_file = lambda name: outside if str(name) else outside.parent.parent
    elif problem == "different-record":
        distribution.locate_file = lambda name: tmp_path / "missing" / str(name)
    elif problem == "metadata-outside":
        distribution.locate_file = lambda name: binary if str(name) else tmp_path / "foreign"
    elif problem in {"not-executable", "writable"}:
        binary.chmod(0o600 if problem == "not-executable" else 0o777)
    elif problem == "foreign-owner":
        owner = module.os.getuid()
        monkeypatch.setattr(module.os, "getuid", lambda: owner + 1)
    with pytest.raises(RuntimeError, match=r"locked|wheel"):
        module.main()
    assert output.read_text() == "prior-entry\n"
    assert not marker.exists()


def test_only_coverage_and_unit_bearing_python_cells_select_locked_node():
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    coverage = jobs["coverage"]
    steps = coverage["steps"]
    sync = next(step for step in steps if step.get("run", "").startswith("uv sync"))
    selected = next(step for step in steps if step.get("run") == "uv run --no-sync python scripts/ci_node.py")
    assert sync["run"] == "uv sync --frozen --no-build --no-dev --group test --group build"
    assert (
        steps.index(sync)
        < steps.index(selected)
        < next(i for i, step in enumerate(steps) if step.get("run") == "make test")
    )
    assert coverage["timeout-minutes"] == 10
    assert coverage["env"]["UV_NO_SYNC"] == "1"
    test = jobs["test"]
    assert test["env"]["CI_NODE_GROUP_ARGUMENT"] == "${{ matrix.levels != 'integration' && '--group=build' || '' }}"
    selected = next(step for step in test["steps"] if step.get("run") == "uv run --no-sync python scripts/ci_node.py")
    assert selected["if"] == "matrix.levels != 'integration'"
    assert (
        next(step for step in test["steps"] if "bootstrap-pyyaml.sh" in step.get("run", ""))["if"]
        == "matrix.python == '3.15'"
    )
    for name, job in jobs.items():
        if name not in {"test", "coverage"}:
            assert not any("scripts/ci_node.py" in step.get("run", "") for step in job.get("steps", [])), name


@pytest.mark.parametrize("python", ["3.12", "3.15"])
@pytest.mark.parametrize("level", ["unit", "integration", ""])
def test_python_sync_adds_only_existing_build_group_for_unit_bearing_cells(tmp_path, python, level):
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["test"]["steps"]
    name = (
        "Install Python 3.15 dependencies with the verified PyYAML compatibility build"
        if python == "3.15"
        else "Install wheel dependencies"
    )
    command = next(step["run"] for step in steps if step.get("name") == name)
    # The PyYAML bootstrap itself is covered by its owning tests; inspect this sync's exact argument expansion.
    command = command.splitlines()[0]
    record = tmp_path / "args.json"
    uv = tmp_path / "uv"
    import sys

    uv.write_text(
        f"#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\n"
        f"Path({str(record)!r}).write_text(json.dumps(sys.argv[1:]))\n"
    )
    uv.chmod(0o700)
    subprocess.run(
        ["/bin/bash", "-eu", "-c", command],
        env={"PATH": str(tmp_path), "CI_NODE_GROUP_ARGUMENT": "" if level == "integration" else "--group=build"},
        check=True,
        timeout=2,
    )
    expected = ["sync", "--frozen", "--no-build", "--no-dev", "--group", "test"]
    if level != "integration":
        expected.append("--group=build")
    if python == "3.15":
        expected.extend(["--no-install-package", "pyyaml"])
    assert json.loads(record.read_text()) == expected


@pytest.mark.parametrize("problem", ["module-missing", "not-venv", "distribution-missing"])
def test_missing_runtime_or_controller_environment_never_exports(selector, monkeypatch, problem):
    module, _distribution, _binary, output, marker = selector
    if problem == "not-venv":
        monkeypatch.setattr(module.sys, "base_prefix", module.sys.prefix)
    elif problem == "distribution-missing":

        def absent_distribution(_name):
            raise module.importlib.metadata.PackageNotFoundError

        monkeypatch.setattr(module.importlib.metadata, "distribution", absent_distribution)
    else:

        def absent_module():
            msg = "missing installed module"
            raise module.build.WebError(msg)

        monkeypatch.setattr(module.build, "node_bin", absent_module)
    with pytest.raises(RuntimeError, match=r"locked|wheel"):
        module.main()
    assert output.read_text() == "prior-entry\n"
    assert not marker.exists()


def test_independent_plugin_template_keeps_its_existing_runtime_setup():
    spec = importlib.util.spec_from_file_location("node_template", ROOT / "scripts/sync_template.py")
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    generated = sync.ci_workflow()
    assert "scripts/ci_node.py" not in generated
    assert "CI_NODE_GROUP_ARGUMENT" not in generated
    assert "--group test --group build" not in generated
    assert generated == (ROOT / "template/.github/workflows/ci.yml.jinja").read_text()
