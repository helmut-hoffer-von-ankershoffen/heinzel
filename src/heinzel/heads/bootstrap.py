"""The versionless `uvx heinzel@latest` quick start runs application commands in the managed app."""

import os
import subprocess
import sys
from pathlib import Path

from pydantic import ValidationError

from heinzel import Config, HeinzelError, Progress, Settings
from heinzel.heads import install
from heinzel.heads._tool_install import inspection_guidance, resolve_target
from heinzel.kernel import Installation


def installed_app() -> Path | None:
    """Find a verified current app without starting its interpreter or reverting independent changes.

    Raises:
        HeinzelError: If a marked installation no longer matches its recorded state.
    """
    uv = install.uv_executable()
    target = resolve_target(uv)
    directory = target.prefix
    if not directory.exists():
        return None
    executable = directory / "bin/hz"
    data = Config.load().settings(Settings, "hz").data_dir
    receipt = data / "standard-app.json"
    if not receipt.exists():
        return None  # An independently installed SDK can be converted without losing its extra requirements.
    reason = "The installed app could not be verified; an older composition will not be reapplied automatically"
    try:
        profile = install.read_profile(str(receipt))
        installation = Installation(Progress(), prefix=directory, uv=uv)
        installation.verify_tool_destination(target.store, target.binary)
        matching = (
            profile.format == install.PROFILE_FORMAT
            and executable.is_file()
            and (directory / "bin/python").is_file()
            and installation.startup_state_matches()
        )
    except (OSError, ValueError, HeinzelError) as exc:
        raise HeinzelError(inspection_guidance(target, uv, reason)) from exc
    if not matching:
        raise HeinzelError(inspection_guidance(target, uv, reason))
    return executable


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
        environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
        environment["PATH"] = f"{executable.parent}{os.pathsep}{os.environ.get('PATH', '')}"
        interpreter = str(executable.parent / "python")
        os.execve(  # noqa: S606 - selected managed interpreter and isolated module; no shell
            interpreter, [interpreter, "-I", "-m", "heinzel", *(arguments or ["ui"])], environment
        )
    except HeinzelError as error:
        sys.stderr.write(f"Heinzelmännchen could not start. {error}\n")
        sys.exit(1)
    except (OSError, ValueError, ValidationError, subprocess.SubprocessError):
        sys.stderr.write(
            "Heinzelmännchen could not start. Check your uv installation and connection, "
            "then retry `uvx heinzel@latest`.\n"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
