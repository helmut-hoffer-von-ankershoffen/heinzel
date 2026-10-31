# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Keep Core-root PR and agent checks within the owning projects for plugin-only changes."""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

TOOLKIT = (
    "heinzel-plugin-tools @ https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/download/"
    "tools-v0.2.1/heinzel_plugin_tools-0.2.1-py3-none-any.whl"
    "#sha256=eebaf555c0c3b39cb13d28ae5ea146ec459e06d1b96cf8ba4499ffea4247aab7"
)


def resolve_base(base: str) -> str:
    """Resolve a revision once so Git and Make receive only a canonical commit identity.

    Raises:
        ValueError: If the input is option-shaped or Git returned an invalid identity.
    """
    if base.startswith("-"):
        msg = "The base must be a revision, not a Git option"
        raise ValueError(msg)
    revision = subprocess.check_output(  # noqa: S603 - fixed Git operation, revision explicitly separated from options
        ["git", "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}"],  # noqa: S607 - repository Git from PATH
        text=True,
        timeout=30,
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", revision):
        msg = "Git returned an invalid commit identity"
        raise ValueError(msg)
    return revision


def changed_paths(mode: str, base: str) -> list[str]:
    """Include deleted, moved and untracked paths without parsing Git's quoted display format."""
    revision = [f"{base}...HEAD"] if mode == "preflight" else ["HEAD"]
    result = subprocess.check_output(  # noqa: S603 - fixed git argument lists, no shell
        ["git", "diff", "--name-only", "--no-renames", "-z", "--end-of-options", *revision, "--"],  # noqa: S607 - git from PATH
        text=True,
        timeout=30,
    )
    if mode == "fast":
        result += subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],  # noqa: S607 - git from PATH
            text=True,
            timeout=30,
        )
    return [path for path in result.split("\0") if path]


def main() -> None:
    """Preserve core checks and common policies while selecting independent owning plugin work."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["fast", "preflight"])
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args()
    try:
        base = resolve_base(args.base) if args.mode == "preflight" else "HEAD"
    except (ValueError, subprocess.CalledProcessError):
        parser.error("Use a valid commit revision for --base")
    changed = changed_paths(args.mode, base)
    base_arguments = [f"BASE={base}"] if args.mode == "preflight" else []
    if not changed or not all(path.startswith("plugins/") for path in changed):
        subprocess.run(  # noqa: S603 - explicit owning commands, no shell
            ["make", "preflight" if args.mode == "preflight" else "check-fast", *base_arguments],  # noqa: S607 - make from PATH
            check=True,
            timeout=1800 if args.mode == "preflight" else 300,
        )
        return
    toolkit = [sys.executable, str(Path(__file__).with_name("plugin_tooling.py")), "--toolkit", TOOLKIT, "hz-plugin"]
    if args.mode == "fast":
        subprocess.run([*toolkit, "check-changed", "--changed", *changed], check=True, timeout=300)  # noqa: S603 - pinned toolkit, literal paths
        return
    # These policies apply to plugin source too; no core build, native job or test suite runs.
    subprocess.run(["make", "commits", "secrets", *base_arguments], check=True, timeout=120)  # noqa: S603, S607 - shared policy targets
    subprocess.run(
        [sys.executable, "scripts/check_confidentiality.py"],
        check=True,
        timeout=120,
    )
    plan = json.loads(
        subprocess.check_output([*toolkit, "plan", "--changed", *changed], text=True, timeout=60)  # noqa: S603 - pinned metadata-only planner
    )
    for project in plan["include"]:
        subprocess.run([*toolkit, "preflight", project["name"]], check=True, timeout=1800)  # noqa: S603 - canonical names from the pinned planner


if __name__ == "__main__":
    # Git runs hooks at the repository root; explicit cwd also supports direct invocation elsewhere.
    os.chdir(Path(__file__).resolve().parents[1])
    main()
