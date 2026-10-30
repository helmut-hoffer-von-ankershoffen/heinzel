"""`Installation.hz` starts a real, fresh interpreter, as `hz system update` and the plugin commands do."""

import os
import subprocess
import sys

import pytest

from heinzel import HeinzelError, Progress
from heinzel.kernel import Installation

pytestmark = pytest.mark.integration


def test_hz_runs_a_fresh_interpreter(tmp_path):
    installation = Installation(Progress(), prefix=tmp_path, uv="uv")
    assert installation.hz("--version").startswith("hz ")
    with pytest.raises(HeinzelError, match="failed after the change"):
        installation.hz("no-such-command")


def test_isolated_target_ignores_ambient_pythonpath_and_cwd_but_preserves_product_environment(tmp_path, monkeypatch):
    environment = tmp_path / "tool"
    subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True, capture_output=True, timeout=60)
    site = environment / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
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
