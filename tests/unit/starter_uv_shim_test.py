"""Starter resolver policy stays with uv, while nested acceptance retains the reviewed wrapper."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from heinzel.testing import scaled

pytestmark = pytest.mark.unit


@pytest.fixture
def starter():
    path = Path(__file__).resolve().parents[2] / "scripts/starter-test.py"
    spec = importlib.util.spec_from_file_location("starter_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("arguments", "position"),
    [
        (["run", "python", "child.py"], 1),
        (["tool", "run", "hz-plugin", "acceptance"], 2),
        (["tool", "install", "fixture.whl"], 2),
        (["pip", "install", "fixture.whl"], 2),
        (["pip", "check"], 0),
        (["--version"], 0),
    ],
)
def test_wrapper_does_not_export_resolver_policy_to_descendants(starter, tmp_path, monkeypatch, arguments, position):
    real = tmp_path / "real-uv"
    observed = tmp_path / "observed.json"
    real.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        f"open({str(observed)!r}, 'w').write(json.dumps([sys.argv[1:], os.environ.get('UV_FIND_LINKS')]))\n"
    )
    real.chmod(0o700)
    monkeypatch.setattr(starter.shutil, "which", lambda _name: str(real))
    wrapper = starter.uv_shim(tmp_path / "wrapper", str(tmp_path / "reviewed-wheels")) / "uv"
    subprocess.run(
        [str(wrapper), *arguments],
        env={**os.environ, "UV_FIND_LINKS": "must-not-propagate"},
        check=True,
        timeout=scaled(15),
    )
    actual, inherited = json.loads(observed.read_text())
    assert inherited is None
    expected = arguments.copy()
    if position:
        expected[position:position] = ["--find-links", str(tmp_path / "reviewed-wheels")]
    assert actual == expected


def test_nested_uv_run_keeps_policy_out_of_child_and_retains_explicit_wrapper(starter, tmp_path, monkeypatch):
    real = Path(sys.executable).parent / "uv"
    assert real.is_file()
    monkeypatch.setattr(starter.shutil, "which", lambda _name: str(real))
    environment = {**os.environ, "UV_FIND_LINKS": str(tmp_path)}
    starter.shim_find_links(environment, tmp_path / "wrapper")
    wrapper = environment["PLUGIN_TEST_UV"]
    probe = (
        "import os, pathlib, subprocess; "
        "assert 'UV_FIND_LINKS' not in os.environ; "
        "uv = os.environ['PLUGIN_TEST_UV']; "
        "env = dict(os.environ, PATH=str(pathlib.Path(uv).parent)+os.pathsep+os.environ['PATH']); "
        "subprocess.run([uv, '--version'], env=env, check=True, timeout=5)"
    )
    subprocess.run(
        [wrapper, "run", "--offline", "--no-project", "--python", sys.executable, "python", "-c", probe],
        env=environment,
        check=True,
        timeout=scaled(15),
    )


def test_journey_owns_its_cache_without_changing_the_callers_cache(starter, tmp_path, monkeypatch):
    caller_cache = str(tmp_path / "caller-cache")
    destination = tmp_path / "journey"
    destination.mkdir()
    monkeypatch.setenv("UV_CACHE_DIR", caller_cache)
    monkeypatch.delenv("UV_FIND_LINKS", raising=False)

    class FirstCommandError(Exception):
        pass

    def inspect_first_command(*args, **kwargs):
        assert kwargs["env"]["UV_CACHE_DIR"] == str(destination / "uv-cache")
        raise FirstCommandError

    monkeypatch.setattr(starter.subprocess, "run", inspect_first_command)
    with pytest.raises(FirstCommandError):
        starter.journey("fixture-core", None, tmp_path, destination)
    assert os.environ["UV_CACHE_DIR"] == caller_cache
