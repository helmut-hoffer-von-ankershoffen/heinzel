# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Keep Core-root PR and agent checks within the owning projects for plugin-only changes."""

import argparse
import json
import os
import subprocess
from pathlib import Path

TOOLKIT = (
    "heinzel-plugin-tools @ https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/download/"
    "tools-v0.2.1/heinzel_plugin_tools-0.2.1-py3-none-any.whl"
    "#sha256=eebaf555c0c3b39cb13d28ae5ea146ec459e06d1b96cf8ba4499ffea4247aab7"
)


def changed_paths(mode: str, base: str) -> list[str]:
    """Include deleted, moved and untracked paths without parsing Git's quoted display format."""
    revision = [f"{base}...HEAD"] if mode == "preflight" else ["HEAD"]
    result = subprocess.check_output(  # noqa: S603 - fixed git argument lists, no shell
        ["git", "diff", "--name-only", "--no-renames", "-z", *revision],  # noqa: S607 - git from PATH
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
    changed = changed_paths(args.mode, args.base)
    if not changed or not all(path.startswith("plugins/") for path in changed):
        subprocess.run(  # noqa: S603 - explicit owning commands, no shell
            ["make", "preflight" if args.mode == "preflight" else "check-fast", f"BASE={args.base}"],  # noqa: S607 - make from PATH
            check=True,
            timeout=1800 if args.mode == "preflight" else 300,
        )
        return
    toolkit = ["uv", "run", "--quiet", "--no-project", "--with", TOOLKIT, "hz-plugin"]
    if args.mode == "fast":
        subprocess.run([*toolkit, "check-changed", "--changed", *changed], check=True, timeout=300)  # noqa: S603 - pinned toolkit, literal paths
        return
    # These policies apply to plugin source too; no core build, native job or test suite runs.
    subprocess.run(["make", "commits", "secrets", f"BASE={args.base}"], check=True, timeout=120)  # noqa: S603, S607 - shared policy targets
    subprocess.run(
        ["uv", "run", "--script", "scripts/check_confidentiality.py"],  # noqa: S607 - pinned script runner
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
