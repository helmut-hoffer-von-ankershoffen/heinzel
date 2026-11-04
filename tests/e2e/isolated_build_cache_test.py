"""Real Hatchling wheel and editable builds share a complete, hash-verified private backend cache."""

import os
import re
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]


def test_standalone_make_build_prepares_a_cold_cache_for_sdist_and_wheel(tmp_path):
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    (project / "src/fixture_build").mkdir(parents=True)
    source = b'VALUE = "standalone"\n'
    (project / "src/fixture_build/__init__.py").write_bytes(source)
    (project / "scripts/build.py").write_bytes((ROOT / "scripts/build.py").read_bytes())
    (project / "pyproject.toml").write_text(
        '[project]\nname = "fixture-build"\nversion = "1.0.0"\nrequires-python = ">=3.12"\n'
        '[dependency-groups]\nbuild = ["hatchling>=1.32.4", "editables>=0.5"]\n'
        '[build-system]\nrequires = ["hatchling>=1.32.4"]\nbuild-backend = "hatchling.build"\n'
        '[tool.hatch.build.targets.wheel]\npackages = ["src/fixture_build"]\n'
    )
    # Use the actual standalone recipe; this Python-only fixture has no frontend assets to build.
    recipe = re.search(r"(?m)^build:.*\n(?:\t.*\n)+", (ROOT / "Makefile").read_text())
    assert recipe is not None
    (project / "Makefile").write_text(recipe[0] + "\nweb-build:\n\t@:\n")
    env = {
        **os.environ,
        "UV_CACHE_DIR": str(tmp_path / "lock-cache"),
        "UV_PROJECT_ENVIRONMENT": str(project / ".venv"),
        "UV_PYTHON": sys.executable,
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_NO_CONFIG": "1",
        "UV_FROZEN": "0",
    }
    env.pop("VIRTUAL_ENV", None)

    def run(*command):
        result = subprocess.run(command, cwd=project, env=env, capture_output=True, text=True, timeout=60, check=False)
        assert result.returncode == 0, result.stdout + result.stderr

    run("uv", "lock")
    # Lock resolution cannot accidentally supply build artifacts: build starts with a different, empty cache.
    env.update(UV_CACHE_DIR=str(tmp_path / "build-cache"), UV_FROZEN="1")
    run("make", "--no-print-directory", "build")
    env["UV_OFFLINE"] = "1"
    run("make", "--no-print-directory", "build")
    assert not (project / ".venv").exists()
    [wheel] = (project / "dist").glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        assert archive.read("fixture_build/__init__.py") == source
    [sdist] = (project / "dist").glob("*.tar.gz")
    with tarfile.open(sdist) as archive:
        member = archive.extractfile("fixture_build-1.0.0/src/fixture_build/__init__.py")
        assert member is not None
        assert member.read() == source


def test_wheel_preparation_also_supplies_the_isolated_editable_backend(tmp_path):
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    (project / "src/fixture_build").mkdir(parents=True)
    (project / "src/fixture_build/__init__.py").write_text('VALUE = "isolated"\n')
    (project / "scripts/build.py").write_bytes((ROOT / "scripts/build.py").read_bytes())
    (project / "pyproject.toml").write_text(
        '[project]\nname = "fixture-build"\nversion = "1.0.0"\nrequires-python = ">=3.12"\n'
        '[dependency-groups]\nbuild = ["hatchling>=1.32.4", "editables>=0.5"]\n'
        '[build-system]\nrequires = ["hatchling>=1.32.4"]\nbuild-backend = "hatchling.build"\n'
        '[tool.hatch.build.targets.wheel]\npackages = ["src/fixture_build"]\n'
    )
    env = {
        **os.environ,
        "UV_CACHE_DIR": str(tmp_path / "cache"),
        "UV_PROJECT_ENVIRONMENT": str(project / ".venv"),
        "UV_PYTHON": sys.executable,
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_NO_CONFIG": "1",
    }
    env.pop("VIRTUAL_ENV", None)

    def run(*command, offline=False, check=True, create_lock=False):
        result = subprocess.run(
            command,
            cwd=project,
            env={**env, "UV_FROZEN": "0" if create_lock else "1", **({"UV_OFFLINE": "1"} if offline else {})},
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    # The previous prepare command downloaded wheel backends but left editables absent.
    # Only create this private fixture's initial lock; every build and install stays frozen.
    run("uv", "lock", create_lock=True)
    run("uv", "build", "--wheel", "--out-dir", str(tmp_path / "old-wheel"))
    old = run("uv", "run", "--frozen", "python", "-c", "import fixture_build", offline=True, check=False)
    assert old.returncode != 0
    assert "editables" in old.stderr

    helper = (sys.executable, str(project / "scripts/build.py"))
    # A genuinely empty cache has neither lock-resolution metadata nor backend artifacts.
    env["UV_CACHE_DIR"] = str(tmp_path / "prepared-cache")
    run(*helper, "sync")
    # Preparation remains valid when the controller exists, including after registry access is removed.
    run(*helper, "sync", offline=True)
    result = run(
        "uv", "run", "--frozen", "python", "-c", "import fixture_build; print(fixture_build.VALUE)", offline=True
    )
    assert result.stdout.strip() == "isolated"
    run(*helper, "--wheel", "--out-dir", str(tmp_path / "new-wheel"), offline=True)
    [wheel] = (tmp_path / "new-wheel").glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        assert archive.read("fixture_build/__init__.py") == b'VALUE = "isolated"\n'

    # Every backend dependency must have a matching locked hash, even when cached.
    lock = tomllib.loads((project / "uv.lock").read_text())
    assert any(package["name"] == "editables" and package["wheels"] for package in lock["package"])
    constraints = tmp_path / "wrong-hash.txt"
    backend_version = next(package["version"] for package in lock["package"] if package["name"] == "hatchling")
    run(
        "uv",
        "export",
        "--frozen",
        "--only-group",
        "build",
        "--no-header",
        "--no-annotate",
        "--output-file",
        str(constraints),
    )
    invalid = constraints.read_text()
    backend = next(package for package in lock["package"] if package["name"] == "hatchling")
    assert backend["version"] == backend_version
    for artifact in [backend["sdist"], *backend["wheels"]]:
        invalid = invalid.replace(artifact["hash"], "sha256:" + "0" * 64)
    constraints.write_text(invalid)
    rejected = run(
        "uv",
        "build",
        "--wheel",
        "--build-constraints",
        str(constraints),
        "--require-build-hashes",
        "--out-dir",
        str(tmp_path / "rejected"),
        offline=True,
        check=False,
    )
    assert rejected.returncode != 0
    assert not list((tmp_path / "rejected").glob("*.whl"))
