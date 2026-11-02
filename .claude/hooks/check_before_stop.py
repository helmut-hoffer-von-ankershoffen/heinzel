"""Stop hook: an agent may only finish after fast checks pass for the changed owners.

Core-root plugin-only changes use metadata scope selection; owning and governed directories use their own Makefile.
It runs when Python, TOML, YAML or template files changed. Exit code 2 keeps the agent working, with the failure as
the reason; `stop_hook_active` prevents an endless loop. Where the host queues heavy checks with `hz-gate`, this waits
its turn so it adds no load; if the turn doesn't come in time, it defers to the pre-push hook, which runs them anyway.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

WATCHED = (".py", ".toml", ".yml", ".yaml", ".jinja")
TIMEOUT = 300


def main() -> int:
    event = json.load(sys.stdin)
    if event.get("stop_hook_active") or not (shutil.which("make") and shutil.which("uv")):
        return 0
    cwd = event.get("cwd") or None
    changed = subprocess.run(
        ["git", "status", "--porcelain", "-z", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        timeout=30,
    ).stdout
    records = iter(changed.split("\0"))
    paths = []
    for record in records:
        paths.append(record[3:])
        if "R" in record[:2] or "C" in record[:2]:
            paths.append(next(records, ""))
    if not any(path.endswith(WATCHED) for path in paths):
        return 0
    scope = Path(cwd or ".") / "scripts/scoped_checks.py"
    command = (
        ["uv", "run", "--no-project", "--script", str(scope), "fast"] if scope.is_file() else ["make", "check-fast"]
    )
    gate = shutil.which("hz-gate")
    try:
        check = subprocess.run(
            [gate, *command] if gate else command, capture_output=True, text=True, check=False, cwd=cwd, timeout=TIMEOUT
        )
    except subprocess.TimeoutExpired:
        if not gate:
            raise
        print("Fast checks did not get a turn in hz-gate; the pre-push hook runs them.", file=sys.stderr)
        return 0
    if check.returncode != 0:
        tail = "\n".join((check.stdout + check.stderr).splitlines()[-60:])
        print(f"Fast checks fail; fix them before finishing:\n{tail}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
