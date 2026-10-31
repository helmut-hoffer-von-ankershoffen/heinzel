"""Private managed Python for isolated uv-tool lifecycle fixtures."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from heinzel.kernel._installation_continuity import external_python


@pytest.fixture(scope="session")
def fixture_managed_python(tmp_path_factory):
    """Provision an interpreter under the test's own private directory and verify its physical path."""
    root = tmp_path_factory.mktemp("managed-python").resolve(strict=True)
    home = root / "home"
    home.mkdir(mode=0o700)
    directory = home / "pythons"
    env = {
        **os.environ,
        "HOME": str(home),
        "UV_PYTHON_INSTALL_DIR": str(directory),
        "UV_CACHE_DIR": str(root / "cache"),
        "UV_NO_CONFIG": "1",
    }
    env.pop("VIRTUAL_ENV", None)
    env.pop("UV_PYTHON", None)
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    subprocess.run(
        ["uv", "--color", "never", "python", "install", "--no-bin", "--install-dir", str(directory), version],
        env=env,
        check=True,
        timeout=180,
    )
    result = subprocess.run(
        ["uv", "--color", "never", "python", "find", "--managed-python", version],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    python = Path(result.stdout.strip()).resolve(strict=True)
    if not python.is_relative_to(directory.resolve(strict=True)):
        pytest.fail("uv did not select the private managed fixture interpreter")
    external_python(python)
    return directory, python
