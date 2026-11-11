"""`Installation.hz` starts a real, fresh interpreter, as `hz system update` and the plugin commands do."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import heinzel
from heinzel import HeinzelError, Progress
from heinzel.kernel import Installation

pytestmark = pytest.mark.integration


@pytest.fixture
def target_environment(tmp_path):
    environment = tmp_path / "tool"
    # Both callers populate site-packages directly. Bootstrapping pip adds a subprocess and unused files.
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(environment)],
        check=True,
        capture_output=True,
        timeout=60,
    )
    site = environment / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    assert not (site / "pip").exists()
    return environment, site


def test_hz_runs_a_fresh_interpreter(tmp_path):
    installation = Installation(Progress(), prefix=tmp_path, uv="uv")
    assert installation.hz("--version").startswith("hz ")
    with pytest.raises(HeinzelError, match="failed after the change"):
        installation.hz("no-such-command")


def test_isolated_target_ignores_ambient_pythonpath_and_cwd_but_preserves_product_environment(
    tmp_path, monkeypatch, target_environment
):
    environment, site = target_environment
    target = site / "heinzel"
    target.mkdir(parents=True)
    (target / "__init__.py").write_text("")
    (target / "__main__.py").write_text(
        "import os; "
        "print('TARGET', os.environ.get('HZ_CONFIG') == 'fixture-config', "
        "'HZ_CREDENTIAL_TEST_TOKEN' not in os.environ, os.environ.get('HZ_UPDATE_CHECK') == 'false')"
    )
    for parent in (tmp_path / "foreign", tmp_path / "working"):
        module = parent / "heinzel"
        module.mkdir(parents=True)
        (module / "__init__.py").write_text("")
        (module / "__main__.py").write_text("print('FOREIGN')")
    monkeypatch.chdir(tmp_path / "working")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "foreign"))
    monkeypatch.setenv("HZ_CONFIG", "fixture-config")
    monkeypatch.setenv("HZ_CREDENTIAL_TEST_TOKEN", "fixture-secret")
    monkeypatch.setenv("HZ_UPDATE_CHECK", "true")
    monkeypatch.setattr(sys, "executable", os.fspath(environment / "bin/python"))
    assert Installation(Progress(), prefix=environment, uv="unused").hz("--version").strip() == "TARGET True True True"


@pytest.mark.parametrize("dont_write_bytecode", [False, True])
def test_plugin_inventory_preserves_callers_bytecode_policy(
    tmp_path, monkeypatch, dont_write_bytecode, target_environment
):
    environment, site = target_environment
    # A private installed-plugin fixture, using the real SDK and dependency set already under test.
    dependency_site = Path(pytest.__file__).resolve().parents[1]
    sdk_source = Path(heinzel.__file__).resolve().parents[1]
    source = tmp_path / "plugin-source"
    fixture = Path(__file__).parents[1] / "fixtures/heinzel-dummy/src/heinzel_dummy"
    shutil.copytree(fixture, source / "heinzel_dummy")
    (site / "test-sdk.pth").write_text(f"{dependency_site}\n{sdk_source}\n{source}\n")
    distribution = site / "heinzel_dummy-1.2.3.dist-info"
    distribution.mkdir()
    (distribution / "METADATA").write_text("Metadata-Version: 2.1\nName: heinzel-dummy\nVersion: 1.2.3\n")
    (distribution / "entry_points.txt").write_text("[heinzel.modules]\ndummy = heinzel_dummy\n")
    before = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    monkeypatch.setattr(sys, "executable", os.fspath(environment / "bin/python"))
    monkeypatch.setattr(sys, "dont_write_bytecode", dont_write_bytecode)
    inventory = json.loads(
        Installation(Progress(), prefix=environment, uv="unused").hz("system", "plugin", "list", "--json")
    )
    assert any(item["distribution"] == "heinzel-dummy" for item in inventory)
    after = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    assert all(after[path] == data for path, data in before.items())
    caches = list(source.rglob("*.pyc"))
    if dont_write_bytecode:
        assert after == before
        assert not caches
    else:
        assert caches, "The control must prove that the inventory actually imported the plugin source"
