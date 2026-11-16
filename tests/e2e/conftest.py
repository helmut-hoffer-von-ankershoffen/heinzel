"""Private managed Python for isolated uv-tool lifecycle fixtures."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from heinzel.kernel._installation_continuity import external_python


def copy_managed_installation(installation, copied):
    """Copy only self-contained links, remapping them into the private installation."""
    links = []
    for link in installation.rglob("*"):
        if link.is_symlink():
            target = link.resolve(strict=True)
            if not target.is_relative_to(installation):
                msg = "the managed interpreter contains a link outside its installation"
                raise ValueError(msg)
            links.append((link.relative_to(installation), target.relative_to(installation)))
    shutil.copytree(installation, copied, symlinks=True)
    for link, target in links:
        copied_link = copied / link
        copied_link.unlink()
        copied_link.symlink_to(os.path.relpath(copied / target, copied_link.parent))


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
    version = ".".join(map(str, sys.version_info[:3]))
    # CI has already provisioned the managed interpreter for the test runner. Copy that installation into the
    # private fixture rather than downloading it again inside an individual test's outer timeout.
    source_env = {**os.environ, "UV_NO_CONFIG": "1"}
    source_env.pop("VIRTUAL_ENV", None)
    source_env.pop("UV_PYTHON", None)
    managed = Path(
        subprocess.run(
            ["uv", "--color", "never", "python", "dir"],
            env=source_env,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
    ).resolve(strict=True)
    source = Path(
        subprocess.run(
            [
                "uv",
                "--color",
                "never",
                "python",
                "find",
                "--no-project",
                "--managed-python",
                "--no-python-downloads",
                version,
            ],
            env=source_env,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
    ).resolve(strict=True)
    installation = source.parent.parent
    if not installation.is_relative_to(managed) or installation.parent != managed:
        pytest.fail("uv did not select a managed source interpreter")
    if source != Path(sys._base_executable).resolve(strict=True):
        pytest.fail("uv selected a different interpreter from the test runner")
    source_identity = external_python(source)
    directory.mkdir(mode=0o700)
    copied = directory / installation.name
    copy_managed_installation(installation, copied)
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
    if external_python(python)[1] != source_identity[1]:
        pytest.fail("the private managed interpreter differs from its source")
    copied_version = subprocess.run(
        [str(python), "-I", "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()
    if copied_version != ".".join(map(str, sys.version_info[:3])):
        pytest.fail("the private managed interpreter version differs from the test runner")
    return directory, python


@pytest.fixture(scope="session", autouse=True)
def reviewed_pyyaml(tmp_path_factory):
    """Python 3.15 has no PyYAML wheel: uv installs in these tests find the reviewed build, not a source build."""
    import fcntl

    shared = tmp_path_factory.getbasetemp().parent
    wheels = shared / "reviewed-pyyaml"
    with (shared / "reviewed-pyyaml.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (wheels / "complete").exists():
            root = Path(__file__).resolve().parents[2]
            subprocess.run(
                ["bash", str(root / "scripts/bootstrap-pyyaml.sh"), "--wheel-dir", str(wheels)],
                cwd=root,
                check=True,
                timeout=300,
            )
            (wheels / "complete").touch()
    previous = os.environ.get("UV_FIND_LINKS")
    os.environ["UV_FIND_LINKS"] = str(wheels)
    yield
    if previous is None:
        os.environ.pop("UV_FIND_LINKS", None)
    else:
        os.environ["UV_FIND_LINKS"] = previous
