"""Private managed Python for isolated uv-tool lifecycle fixtures."""

import fcntl
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from heinzel.kernel.install._installation_continuity import external_python


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


def python_environment(environ, home, directory, cache):
    """Remove ambient interpreter selection while preserving uv's transport settings."""
    env = {
        **environ,
        "HOME": str(home),
        "UV_PYTHON_INSTALL_DIR": str(directory),
        "UV_CACHE_DIR": str(cache),
        "UV_NO_CONFIG": "1",
    }
    env.pop("VIRTUAL_ENV", None)
    env.pop("UV_PYTHON", None)
    return env


def uv_python_path(args, env):
    path = Path(
        subprocess.run(
            ["uv", "--color", "never", "python", *args],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    )
    if not path.is_absolute():
        pytest.fail("uv did not return an absolute interpreter path")
    return path


def find_managed_source(version, directory, env):
    source = uv_python_path(
        ["find", "--no-project", "--managed-python", "--no-python-downloads", version], env
    ).resolve(strict=True)
    if source.parent.parent.parent != directory.resolve(strict=True):
        pytest.fail("uv did not select a managed source interpreter")
    return source


def managed_source(version, base_executable, shared, environ):
    """Reuse an exact managed runner or provision one private seed for the pytest run."""
    source_env = {**environ, "UV_NO_CONFIG": "1"}
    source_env.pop("VIRTUAL_ENV", None)
    source_env.pop("UV_PYTHON", None)
    managed = uv_python_path(["dir"], source_env).resolve()
    runner = base_executable.resolve(strict=True)
    if runner.parent.parent.parent == managed:
        source = find_managed_source(version, managed, source_env)
        if source != runner:
            pytest.fail("uv selected a different interpreter from the managed test runner")
        return source

    # Hosted images may satisfy uv's project environment with Homebrew/system Python, leaving the managed directory
    # absent. Provision the exact patch once under the xdist run's owned temp directory, never copy system Python.
    seed = shared / "fixture-python-source"
    with (shared / "fixture-python-source.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        seed.mkdir(mode=0o700, exist_ok=True)
        home = seed / "home"
        home.mkdir(mode=0o700, exist_ok=True)
        directory = home / "pythons"
        completed, failed = seed / "complete", seed / "failed"
        if failed.exists():
            pytest.fail("shared managed fixture interpreter provisioning already failed")
        env = python_environment(environ, home, directory, seed / "cache")
        if not completed.exists():
            try:
                subprocess.run(
                    [
                        "uv",
                        "--color",
                        "never",
                        "python",
                        "install",
                        "--no-bin",
                        "--install-dir",
                        str(directory),
                        version,
                    ],
                    env=env,
                    check=True,
                    timeout=60,
                )
                source = find_managed_source(version, directory, env)
            except (OSError, subprocess.SubprocessError, pytest.fail.Exception):
                failed.touch(mode=0o600)
                raise
            completed.touch(mode=0o600)
            return source
        return find_managed_source(version, directory, env)


@pytest.fixture(scope="session")
def fixture_managed_python(tmp_path_factory):
    """Copy the approved managed interpreter into this worker's own private directory."""
    root = tmp_path_factory.mktemp("managed-python").resolve(strict=True)
    home = root / "home"
    home.mkdir(mode=0o700)
    directory = home / "pythons"
    env = python_environment(os.environ, home, directory, root / "cache")
    version = ".".join(map(str, sys.version_info[:3]))
    base = tmp_path_factory.getbasetemp()
    shared = base.parent if "PYTEST_XDIST_WORKER" in os.environ else base
    source = managed_source(version, Path(sys._base_executable), shared, os.environ)
    installation = source.parent.parent
    source_identity = external_python(source)
    directory.mkdir(mode=0o700)
    copied = directory / installation.name
    copy_managed_installation(installation, copied)
    python = uv_python_path(
        ["find", "--no-project", "--managed-python", "--no-python-downloads", version], env
    ).resolve(strict=True)
    if python.parent.parent != copied.resolve(strict=True):
        pytest.fail("uv did not select the private managed fixture interpreter")
    if external_python(python)[1] != source_identity[1]:
        pytest.fail("the private managed interpreter differs from its source")
    copied_version = subprocess.run(
        [str(python), "-I", "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    if copied_version != version:
        pytest.fail("the private managed interpreter version differs from the test runner")
    return directory, python


@pytest.fixture(scope="session", autouse=True)
def reviewed_pyyaml(tmp_path_factory):
    """Python 3.15 has no PyYAML wheel: uv installs in these tests find the reviewed build, not a source build."""
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
