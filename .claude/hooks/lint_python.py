"""PostToolUse hook: format and lint the Python files an agent writes; report what's left back to the agent.

For Edit and Write that is the edited file; for Bash (sed, heredocs, scripts) it is every changed Python file that
was modified in the last two minutes. Exit code 2 shows stderr to the agent as feedback (the edit already happened,
so nothing is blocked).
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

RECENT = 120  # seconds


def changed_by_bash(cwd: str | None) -> list[Path]:
    """Python files git sees as changed that were written moments ago."""
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        timeout=30,
    ).stdout
    root = Path(cwd or ".")
    paths = [root / line[3:].split(" -> ")[-1] for line in status.splitlines()]
    now = time.time()
    return [p for p in paths if p.suffix == ".py" and p.is_file() and now - p.stat().st_mtime < RECENT]


def main() -> int:
    event = json.load(sys.stdin)
    if not shutil.which("uv"):
        return 0
    cwd = event.get("cwd") or None
    if event.get("tool_name") == "Bash":
        paths = changed_by_bash(cwd)
    else:
        path = Path(event.get("tool_input", {}).get("file_path", ""))
        paths = [path] if path.suffix == ".py" and path.is_file() else []
    if not paths:
        return 0
    files = [str(p) for p in paths]
    subprocess.run(["uv", "run", "--quiet", "ruff", "format", "--quiet", *files], check=False, cwd=cwd, timeout=60)
    lint = subprocess.run(
        ["uv", "run", "--quiet", "ruff", "check", "--fix", "--quiet", "--output-format", "concise", *files],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        timeout=60,
    )
    if lint.returncode != 0:
        print(f"ruff found problems that need a fix by hand:\n{lint.stdout}{lint.stderr}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
