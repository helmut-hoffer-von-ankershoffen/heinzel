"""The versionless `uvx heinzel@latest` quick start runs application commands in the managed app."""

import json
import os
import subprocess
import sys
from pathlib import Path

from pydantic import ValidationError

from heinzel import Config, HeinzelError, Settings
from heinzel.heads import install


def installed_app() -> Path | None:
    """Find a complete managed app without importing its plugins or contacting a registry."""
    result = subprocess.run(  # noqa: S603 - fixed uv query, no shell
        [install.uv_executable(), "tool", "dir"], capture_output=True, text=True, check=False, timeout=15
    )
    if result.returncode or not result.stdout.strip():
        return None
    directory = Path(result.stdout.strip()) / "heinzel"
    executable = directory / "bin/hz"
    interpreter = directory / "bin/python"
    if not executable.is_file() or not interpreter.is_file():
        return None
    data = Config.load().settings(Settings, "hz").data_dir
    receipt = data / "standard-app.json"
    try:
        profile = install.read_profile(str(receipt))
    except (OSError, ValueError):
        return None
    excluded = install.excluded_plugins(data / "plugins.jsonl")
    required = sorted(item.name for item in profile.artifacts if item.name == "heinzel" or item.name not in excluded)
    # Metadata inspection only: loading provider code belongs to the actual app, after exec.
    code = (
        "import importlib.metadata,json,sys; "
        "names={d.metadata['Name'].lower().replace('_','-') for d in importlib.metadata.distributions()}; "
        "sys.exit(not set(json.loads(sys.argv[1])).issubset(names))"
    )
    check = subprocess.run(  # noqa: S603 - fixed interpreter and code; names are a JSON argument, never executable code
        [str(interpreter), "-c", code, json.dumps(required)], capture_output=True, check=False, timeout=15
    )
    return executable if check.returncode == 0 else None


def main() -> None:
    """Install the standard app on first bare use, then replace this process with its foreground workspace."""
    arguments = sys.argv[1:]
    if arguments in (["--help"], ["--version"]):
        from heinzel.heads.cli import main as cli_main  # noqa: PLC0415 - argument-driven CLI remains independent

        cli_main()
        return
    try:
        executable = installed_app()
        if executable is None:
            sys.stderr.write("Installing Heinzelmännchen and its default plugins…\n")
            status = install.main([])
            if status:
                sys.exit(status)
            executable = installed_app()
        if executable is None:
            sys.stderr.write("Installation did not leave a usable standard app. Retry `uvx heinzel@latest`.\n")
            sys.exit(1)
        environment = dict(os.environ, PATH=f"{executable.parent}{os.pathsep}{os.environ.get('PATH', '')}")
        os.execve(str(executable), [str(executable), *(arguments or ["ui"])], environment)  # noqa: S606 - managed app, no shell
    except (OSError, ValueError, HeinzelError, ValidationError, subprocess.SubprocessError):
        sys.stderr.write(
            "Heinzelmännchen could not start. Check your uv installation and connection, "
            "then retry `uvx heinzel@latest`.\n"
        )
        sys.exit(1)
