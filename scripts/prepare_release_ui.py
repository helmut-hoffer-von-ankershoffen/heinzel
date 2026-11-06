"""Print the exact foreground UI policy patch for one Core release."""

# Project-bound: runs in the project environment (`uv run python`) to inspect the checkout CLI after isolation.

import ast
import difflib
import os
import re
import runpy
import signal
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HARNESS = "scripts/acceptance/run_acceptance.py"
FOREGROUND = ("ui", "serve")
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
ARGUMENT_COUNT = 2


def command_metadata() -> dict[str, Any]:
    """Read the current checkout's command tree with an empty profile and built-ins only.

    Raises:
        ValueError: Core was loaded from outside the intended checkout.
    """
    if "heinzel" in sys.modules:
        msg = "The release interpreter already imported Core before the checkout could be selected."
        raise ValueError(msg)
    source = ROOT / "src"
    sys.path.insert(0, str(source))
    from typer import Context  # noqa: PLC0415 - load runtime only after isolating the probe
    from typer.main import get_command  # noqa: PLC0415

    import heinzel  # noqa: PLC0415 - select the checked-out package first
    from heinzel.heads import cli  # noqa: PLC0415
    from heinzel.heads.cli import _discovery  # noqa: PLC0415
    from heinzel.kernel import discover  # noqa: PLC0415

    for module, relative in (
        (heinzel, "heinzel/__init__.py"),
        (cli, "heinzel/heads/cli/__init__.py"),
        (_discovery, "heinzel/heads/cli/_discovery.py"),
    ):
        if module.__file__ is None or Path(module.__file__).resolve() != source / relative:
            msg = "The CLI metadata did not come from this Core checkout."
            raise ValueError(msg)
    records, _ = discover(plugins=False)
    kernel = heinzel.Kernel(heinzel.Settings(), records, config=heinzel.Config.empty())
    return _discovery.command_tree(kernel, Context(get_command(cli.build(kernel))))


def verify_foreground(metadata: dict[str, Any]) -> None:
    """Require the reviewed foreground path and option shape in real CLI metadata.

    Raises:
        ValueError: The foreground command could not be verified.
    """
    msg = "The current CLI does not verify the reviewed foreground contract: hz ui serve."
    try:
        (ui,) = (node for node in metadata["root"]["commands"] if node["command"] == "hz ui")
        command_name = " ".join(("hz", *FOREGROUND))
        (serve,) = (node for node in ui["commands"] if node["command"] == command_name)
        options = {
            option for parameter in serve["parameters"] for option in parameter["opts"] + parameter["secondary_opts"]
        }
        valid = (
            metadata["schema_version"] == 1
            and ui["hidden"] is False
            and ui["deprecated"] is False
            and serve["hidden"] is False
            and serve["deprecated"] is False
            and {"--port", "--no-browser"} <= options
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError(msg) from None
    if not valid:
        raise ValueError(msg)


def policy_patch(path: Path, version: str) -> str:
    """Return an empty patch for an existing identical row, or insert exactly one literal row.

    Raises:
        ValueError: The version or policy is ambiguous or conflicts with the reviewed foreground command.
    """
    if VERSION.fullmatch(version) is None:
        msg = "The foreground UI contract requires an exact final Core version."
        raise ValueError(msg)
    source = path.read_text(encoding="utf-8")
    (table,) = (
        node.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "FOREGROUND_UI" for target in node.targets)
    )
    if not isinstance(table, ast.Dict) or table.end_lineno is None:
        msg = "The foreground UI policy is not an inline dictionary."
        raise ValueError(msg)
    keys = [ast.literal_eval(key) for key in table.keys if key is not None]
    if any(not isinstance(key, str) for key in keys) or len(keys) != len(set(keys)):
        msg = "The foreground UI policy has invalid or duplicate versions."
        raise ValueError(msg)
    policy = runpy.run_path(str(path))["FOREGROUND_UI"]
    if version in policy:
        if version not in keys or policy[version] != FOREGROUND:
            msg = "The release version already has a conflicting foreground UI policy entry."
            raise ValueError(msg)
        return ""
    before = source.splitlines(keepends=True)
    end = table.end_lineno - 1
    if before[end] != "}\n":
        msg = "The foreground UI policy has an unexpected closing line."
        raise ValueError(msg)
    group, command = FOREGROUND
    after = [*before[:end], f'    "{version}": ("{group}", "{command}"),\n', *before[end:]]
    ast.parse("".join(after))
    return "".join(difflib.unified_diff(before, after, fromfile=f"a/{HARNESS}", tofile=f"b/{HARNESS}"))


def main(version: str) -> str:
    """Plan from the checkout before the release script creates or modifies a branch."""
    with TemporaryDirectory(prefix="hz-release-ui-") as profile:
        os.chdir(profile)
        os.environ.clear()
        os.environ.update(HOME=profile, PATH=os.defpath, NO_COLOR="1")
        patch = policy_patch(ROOT / HARNESS, version)
        verify_foreground(command_metadata())
    return patch


if __name__ == "__main__":
    if not sys.flags.isolated or sys.flags.optimize or len(sys.argv) != ARGUMENT_COUNT:
        sys.exit("Usage: python -I scripts/prepare_release_ui.py VERSION (no -O)")
    signal.alarm(20)
    try:
        sys.stdout.write(main(sys.argv[1]))
    except (ImportError, OSError, SyntaxError, ValueError) as error:
        sys.exit(str(error))
