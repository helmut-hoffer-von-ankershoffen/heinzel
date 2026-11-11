"""Enter the selected SDK in isolated Python mode before importing the application namespace."""

import os
import sys


def _launch(module: str) -> None:
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
    flags = ["-I", "-B"] if sys.dont_write_bytecode else ["-I"]
    os.execve(  # noqa: S606 - selected interpreter, isolated module and argument vector; no shell
        sys.executable,
        [sys.executable, *flags, "-m", module, *sys.argv[1:]],
        environment,
    )


def main() -> None:
    """Open the selected app launcher without importing a checkout from the caller's Python path."""
    _launch("heinzel.heads.bootstrap")


def cli() -> None:
    """Run the independently usable CLI in the selected SDK's interpreter."""
    _launch("heinzel")


def install() -> None:
    """Run the selected SDK's verified standard installer."""
    _launch("heinzel.heads.install")
