"""Build the actual SDK wheel and verify its isolated launcher payload."""

import importlib.util
import stat
import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("pypi_release", ROOT / "scripts/pypi_release.py")
assert SPEC is not None
assert SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def test_current_built_wheel_provides_the_isolated_launchers(tmp_path):
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)], cwd=ROOT, check=True, capture_output=True, timeout=300
    )
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    # SDK wheels built on Linux need not contain the macOS notifier. The release build supplies and validates it.
    with zipfile.ZipFile(tmp_path / f"heinzel-{version}-py3-none-any.whl") as archive:
        release._console_scripts(archive, version)
        launcher = archive.getinfo("_heinzel_launcher.py")
        assert stat.S_ISREG(launcher.external_attr >> 16)
        assert archive.read(launcher)
