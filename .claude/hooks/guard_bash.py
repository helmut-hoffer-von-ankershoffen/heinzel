"""PreToolUse hook: stop Bash commands that repeatedly caused trouble, with the reason and the better way.

Exit code 2 blocks the command and shows stderr to the agent.
"""

import json
import re
import sys

RULES = [
    (
        re.compile(r"git\b.*\bcommit\b.*(--no-verify|\s-n\b)|core\.hooksPath"),
        "Don't bypass the git hooks; fix what they report (make fix, then commit again).",
    ),
    (
        re.compile(r"gh\s+pr\s+create\b.*--base\s+(?!main\b)\S+"),
        "Pull requests target main; stacked ones merge into their base branch. Use make pr TITLE=...",
    ),
    (
        re.compile(r"\|\s*(tail|head|grep)\b[^;&|]*&&\s*(git\s+push|gh\s+pr)"),
        "A pipe hides the exit code of the check before it; make targets are already quiet. Use make pr TITLE=...",
    ),
    (
        re.compile(r"git\s+push\b.*(--force\b|\s-f\b)(?!-with-lease)"),
        "Use --force-with-lease, never --force.",
    ),
]


def main() -> int:
    event = json.load(sys.stdin)
    command = event.get("tool_input", {}).get("command", "")
    for pattern, reason in RULES:
        if pattern.search(command):
            print(f"Blocked: {reason}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
