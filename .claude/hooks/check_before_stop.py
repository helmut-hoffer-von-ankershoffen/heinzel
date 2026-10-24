"""Stop hook: an agent may only finish when `make check-fast` passes (format, lint, contracts, unit tests).

It takes seconds; the full `make check` runs in git hooks and CI. It runs only when Python, TOML or YAML files changed,
in the directory the agent works in (a worktree, possibly). Exit code 2 keeps the agent working, with the failure as the
reason; `stop_hook_active` prevents an endless loop.
"""

import json
import shutil
import subprocess
import sys

WATCHED = (".py", ".toml", ".yml", ".yaml", ".jinja")


def main() -> int:
    event = json.load(sys.stdin)
    if event.get("stop_hook_active") or not (shutil.which("make") and shutil.which("uv")):
        return 0
    cwd = event.get("cwd") or None
    changed = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        timeout=30,
    ).stdout
    if not any(line.rstrip().endswith(WATCHED) for line in changed.splitlines()):
        return 0
    check = subprocess.run(["make", "check-fast"], capture_output=True, text=True, check=False, cwd=cwd, timeout=300)
    if check.returncode != 0:
        tail = "\n".join((check.stdout + check.stderr).splitlines()[-60:])
        print(f"`make check-fast` fails; fix it before finishing:\n{tail}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
