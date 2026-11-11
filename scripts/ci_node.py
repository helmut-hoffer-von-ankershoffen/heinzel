# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Select the installed locked Node wheel for Python tests without starting or reading Node."""

import importlib.metadata
import importlib.util
import json
import os
import re
import stat
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
NODE_RECORD = "nodejs_wheel/bin/node"


def _web_build() -> ModuleType:
    spec = importlib.util.spec_from_file_location("coverage_web_build", ROOT / "scripts/web_build.py")
    if spec is None or spec.loader is None:  # pragma: no cover - adjacent reviewed repository script
        raise ImportError(name="web_build")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _web_build()


def _installed_node(name: str, version: str) -> Path:
    """Inspect distribution metadata and filesystem identity without reading the executable.

    Raises:
        ValueError: If version, RECORD membership, venv location or executable ownership differs.
    """
    distribution = importlib.metadata.distribution(name)
    records = [item for item in distribution.files or () if str(item) == NODE_RECORD]
    if distribution.version != version or len(records) != 1:
        raise ValueError
    prefix = Path(sys.prefix).resolve(strict=True)
    declared = Path(distribution.locate_file(records[0]))
    selected = build.node_bin() / "node"
    canonical = selected.resolve(strict=True)
    if selected.is_symlink() or canonical != declared.resolve(strict=True) or not canonical.is_relative_to(prefix):
        raise ValueError
    if not Path(distribution.locate_file("")).resolve(strict=True).is_relative_to(prefix):
        raise ValueError
    info = canonical.stat()
    if not stat.S_ISREG(info.st_mode) or not os.access(canonical, os.X_OK):
        raise ValueError
    if info.st_uid != os.getuid() or info.st_mode & 0o022 or re.search(r"[\x00-\x1f\x7f]", str(canonical.parent)):
        raise ValueError
    return canonical


def locked_node() -> tuple[Path, str]:
    """Select the exact wheel runtime installed in this controller venv.

    Raises:
        RuntimeError: If the locked runtime cannot be selected safely.
    """
    match = re.fullmatch(r"([^=]+)==([0-9]+\.[0-9]+\.[0-9]+)", build.NODE_REQUIREMENT)
    if match is None or sys.prefix == sys.base_prefix:
        msg = "Python tests require the exact locked Node wheel in its current virtual environment."
        raise RuntimeError(msg)
    name, version = match.groups()
    try:
        return _installed_node(name, version), version
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError, build.WebError) as error:
        msg = "The installed Node wheel does not match the locked version or its owned venv executable."
        raise RuntimeError(msg) from error


def main() -> None:
    """Export only the verified directory after every guard, then record static identity."""
    node, version = locked_node()
    with Path(os.environ["GITHUB_PATH"]).open("a", encoding="utf-8") as output:
        output.write(str(node.parent) + "\n")
    print(
        "node_identity "
        + json.dumps(
            {
                "phase": "python-tests-selected",
                "runtime_version": "unobserved",
                "locked_distribution_version": version,
                "executable": str(node),
            }
        )
    )


if __name__ == "__main__":
    main()
