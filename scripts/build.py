# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Prepare the locked controller and build in isolation from its hash-verified backend cache (ADR 0125).

`prepare` only fills the backend cache; `sync` also prepares the locked editable controller.
Other arguments go to `uv build`,
which stays offline: run `make setup` first when a required backend artifact is missing.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, env: dict[str, str] | None = None) -> None:
    """Run uv in this checkout, preserving its normal cache and interpreter selection."""
    subprocess.run(["uv", *args], cwd=ROOT, env=env, check=True, timeout=300)  # noqa: S603, S607 - fixed executable and trusted build flags


def main(args: list[str]) -> None:
    """Export the lock's build hashes and scope them to the isolated build subprocess."""
    with tempfile.TemporaryDirectory(prefix="heinzel-build-") as directory:
        constraints = str(Path(directory) / "constraints.txt")
        run(
            "export",
            "--frozen",
            "--only-group",
            "build",
            "--no-emit-project",
            "--no-header",
            "--no-annotate",
            "--output-file",
            constraints,
            "--quiet",
        )
        isolation = ("--offline", "--build-constraints", constraints, "--require-build-hashes")
        if args in (["sync"], ["prepare"]):
            # Installing into a disposable environment prepares resolver metadata as well as wheel bytes.
            backend = str(Path(directory) / "backend")
            run("venv", "--quiet", backend)
            run(
                "pip",
                "install",
                "--python",
                str(Path(backend) / "bin/python"),
                "--no-build",
                "--require-hashes",
                "--requirements",
                constraints,
                "--quiet",
            )
            if args == ["sync"]:
                # Download controller dependencies before the project backend is allowed to execute.
                run("sync", "--frozen", "--all-groups", "--no-install-project", "--quiet")
                python = str(Path(os.environ.get("UV_PROJECT_ENVIRONMENT", ".venv")) / "bin" / "python")
                run("pip", "install", "--python", python, "--no-deps", "--editable", ".", *isolation, "--quiet")
        else:
            run("build", *isolation, *args)


if __name__ == "__main__":
    main(sys.argv[1:])
