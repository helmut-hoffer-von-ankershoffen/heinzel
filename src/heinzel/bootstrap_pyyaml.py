"""Build the reviewed PyYAML for a Python that has no upstream PyYAML wheel.

The one place that names the reviewed source distribution, its hash and the build backend pins. It uses the standard
library only and never imports `heinzel`, so it runs as a file before any dependency is installed:

    python -I -S bootstrap_pyyaml.py install --lock uv.lock --python .venv/bin/python
    python -I -S bootstrap_pyyaml.py build --requirements requirements.lock --wheel-dir DIR --python .venv/bin/python
    python -I -S bootstrap_pyyaml.py needed [3.15]   # exit 0 when that Python (default: this one) has no wheel
    python -I -S bootstrap_pyyaml.py pins     # the reviewed pins as JSON

Core's own `scripts/bootstrap-pyyaml.sh` and the plugin toolkit (which reads this file out of the Core wheel) both run
it, so neither keeps a copy of a pin.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

NAME = "pyyaml"
VERSION = "6.0.4"
SOURCE_URL = (
    "https://files.pythonhosted.org/packages/76/b8/c0b780928e2fb1c87e9e375818c6f121da2398b071012d1747be300b83f4/"
    "pyyaml-6.0.4.tar.gz"
)
SOURCE_SHA256 = "e24bf71af8a432d7808fabff134d38dfe16d3cbb41f2873bb4b1e42422de0a1a"
# Upstream publishes wheels of this version for every CPython before this one.
NO_WHEEL_FROM = (3, 15)
# Backend wheels for the explicit, isolated build; no source dependencies.
BACKEND = (
    "setuptools==84.2.1 --hash=sha256:ca140b45d5325a9478f880e351484f62fef15f336462e449ca0c03c873d589c2",
    (
        "wheel==0.48.0 --hash=sha256:3217dcc807155e45db462d7ef2431f5ddda0d7273b700d05a67b271ceb1287ab"
        " --hash=sha256:94800765601e9171bf5d58d066e640662842bcedcbab982b2c90787a2c987322"
    ),
    "packaging==26.3 --hash=sha256:d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c",
)
ARCHIVE = f"{NAME}-{VERSION}.tar.gz"
TAMPERED = "The downloaded PyYAML source differs from the reviewed hash; nothing was built."
NO_SINGLE_WHEEL = "The PyYAML build did not produce exactly one wheel."
DIFFERS = "The lock differs from the reviewed PyYAML bootstrap; update its source pin before building."


def pins() -> dict[str, str | list[str]]:
    """The reviewed pins, for a caller that must not keep a copy of them."""
    return {
        "name": NAME,
        "version": VERSION,
        "source_url": SOURCE_URL,
        "source_sha256": SOURCE_SHA256,
        "no_wheel_from": ".".join(map(str, NO_WHEEL_FROM)),
        "backend": list(BACKEND),
    }


def needed(version: tuple[int, ...] | None = None) -> bool:
    """Whether a Python of this version has no upstream wheel and so needs the reviewed build."""
    return tuple((version or sys.version_info)[:2]) >= NO_WHEEL_FROM


def check_uv_lock(path: Path) -> None:
    """A project resolves its own lock: never replace that selection with a different reviewed version.

    Raises:
        SystemExit: The lock selects another PyYAML than the reviewed one.
    """
    lock = tomllib.loads(path.read_text(encoding="utf-8"))
    packages: list[dict[str, Any]] = lock.get("package", [])
    absent: dict[str, Any] = {}
    package = next((item for item in packages if item.get("name") == NAME), absent)
    source = package.get("sdist", {})
    if (
        package.get("version") != VERSION
        or source.get("url") != SOURCE_URL
        or source.get("hash") != "sha256:" + SOURCE_SHA256
    ):
        raise SystemExit(DIFFERS)


def check_requirements(path: Path) -> None:
    """A hash-pinned requirements file must pin exactly the reviewed version and accept the reviewed source hash.

    Raises:
        SystemExit: The file pins another PyYAML or does not accept the reviewed source.
    """
    text = path.read_text(encoding="utf-8").replace("\\\n", " ")
    entries = [line.split() for line in text.splitlines() if re.match(rf"(?i){NAME}\s*==", line.strip())]
    if len(entries) != 1:
        raise SystemExit(DIFFERS)
    requirement = entries[0][0].lower().replace(" ", "")
    if requirement != f"{NAME}=={VERSION}" or f"--hash=sha256:{SOURCE_SHA256}" not in entries[0]:
        raise SystemExit(DIFFERS)


def _run(command: list[str], env: dict[str, str] | None = None) -> None:
    result = subprocess.run(command, check=False, env=env, timeout=900)  # noqa: S603 - fixed commands, reviewed operands
    if result.returncode:
        raise SystemExit(result.returncode)


def build(work: Path, python: str) -> Path:
    """Download the reviewed source, verify it, and build one wheel with the hash-pinned backend. No credentials.

    Raises:
        SystemExit: The source differs from the reviewed hash, or the build left no single wheel.
    """
    archive = work / ARCHIVE
    _run(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            SOURCE_URL,
            "--output",
            str(archive),
        ]
    )
    if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise SystemExit(TAMPERED)
    backend = work / "backend"
    requirements = work / "backend-requirements.txt"
    requirements.write_text("\n".join(BACKEND) + "\n", encoding="utf-8")
    _run(["uv", "venv", "--python", python, str(backend)])
    interpreter = str(backend / "bin/python")
    _run(
        [
            *("uv", "pip", "install", "--python", interpreter, "--no-build", "--require-hashes"),
            *("--requirements", str(requirements)),
        ]
    )
    environment = {key: value for key, value in os.environ.items() if key != "PYYAML_FORCE_CYTHON"}
    environment["PYYAML_FORCE_LIBYAML"] = "0"
    _run(
        [
            *("uv", "build", "--wheel", "--no-build-isolation", "--python", interpreter),
            *("--out-dir", str(work / "dist"), str(archive)),
        ],
        env=environment,
    )
    wheels = sorted((work / "dist").glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(NO_SINGLE_WHEEL)
    return wheels[0]


def install(wheel: Path, python: str) -> None:
    """Install the built wheel and record it as the registry package it was built from.

    The wheel is installed from a file, so uv would see the lock's registry package as changed and rebuild it from the
    source distribution on the next `uv sync` or `uv run`, with an unpinned backend. Without the direct-URL record it
    is the registry package to every later sync.
    """
    _run(["uv", "pip", "install", "--python", python, "--no-build", "--no-deps", str(wheel)])
    site = subprocess.run(  # noqa: S603 - the interpreter the caller selected
        [python, "-c", 'import sysconfig; print(sysconfig.get_paths()["purelib"])'],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.strip()
    (Path(site) / f"{NAME}-{VERSION}.dist-info" / "direct_url.json").unlink(missing_ok=True)


def _checked(arguments: argparse.Namespace) -> None:
    if arguments.lock:
        check_uv_lock(Path(arguments.lock))
    if arguments.requirements:
        check_requirements(Path(arguments.requirements))


def main(argv: list[str] | None = None) -> int:
    """Run one command; a refusal exits with its reason before any download or backend."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").partition("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("pins", help="print the reviewed pins as JSON")
    target = commands.add_parser("needed", help="exit 0 when a Python has no upstream PyYAML wheel, else 1")
    target.add_argument("python", nargs="?", help="a `major.minor` Python; this interpreter when omitted")
    for name in ("build", "install"):
        command = commands.add_parser(name)
        command.add_argument("--lock", help="a uv.lock that must select the reviewed source")
        command.add_argument("--requirements", help="a hash-pinned requirements file that must accept it")
        command.add_argument("--python", required=name == "install", help="the interpreter the wheel is built for")
        if name == "build":
            command.add_argument("--wheel-dir", required=True, help="where the wheel goes; nothing is installed")
    arguments = parser.parse_args(argv)
    if arguments.command == "pins":
        sys.stdout.write(json.dumps(pins(), indent=2) + "\n")
        return 0
    if arguments.command == "needed":
        version = tuple(map(int, arguments.python.split("."))) if arguments.python else None
        return 0 if needed(version) else 1
    _checked(arguments)  # before any download or backend
    python = arguments.python or os.environ.get("UV_PYTHON") or ".".join(map(str, NO_WHEEL_FROM))
    with tempfile.TemporaryDirectory(prefix="bootstrap-pyyaml-") as temporary:
        wheel = build(Path(temporary), python)
        if arguments.command == "build":
            destination = Path(arguments.wheel_dir)
            destination.mkdir(parents=True, exist_ok=True)
            sys.stdout.write(f"{shutil.copy2(wheel, destination / wheel.name)}\n")
        else:
            install(wheel, arguments.python)
    return 0


if __name__ == "__main__":
    sys.exit(main())
