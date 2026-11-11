"""Fail closed for the one-shot, read-only sign-in diagnostic."""

import os
import platform
import re
import subprocess
import sys
from pathlib import Path


def authorized(env, head, system, machine):
    """Bind a single macOS ARM checkout to an explicit requested commit."""
    expected = env.get("FOCUSED_SELECTOR", "")
    return (
        env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and env.get("GITHUB_REPOSITORY") == "helmut-hoffer-von-ankershoffen/heinzel"
        and env.get("GITHUB_REF") == "refs/heads/ci/706-signin-pair-diagnostic"
        and env.get("FOCUSED_REF") == "ci/706-signin-pair-diagnostic"
        and env.get("REQUESTED_OS") == "macos-latest"
        and env.get("FOCUSED_KIND") == "pytest"
        and env.get("FOCUSED_REPEAT") == "1"
        and env.get("FOCUSED_STRESS") == "false"
        and system == "Darwin"
        and machine == "arm64"
        and re.fullmatch(r"[0-9a-f]{40}", expected) is not None
        and expected == env.get("GITHUB_SHA") == head
    )


def report(directory):
    """Emit only fixed, capped phase markers; never print raw diagnostic output."""
    marker = re.compile(
        r"SIGN_IN_(?:BROWSER (?:chromium|webkit) (?:route-enter|fetch-start|fetch-return) \d{13}|"
        r"SERVER (?:handler-enter|handler-pending|handler-return|prepare-enter|prepare-refused|prepare-return) \d{13}|"
        r"STDERR_PENDING \d+|CLEANUP_UNCERTAIN|STACK (?:_chatgpt_signin[.]py|_chatgpt[.]py|rich/console[.]py|"
        r"logging/__init__[.]py|h11_impl[.]py):\d{1,6})"
    )
    path = Path(directory) / "sanitized-signin-markers.txt"
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 8192:
        sys.stdout.write("Sign-in diagnostic markers unavailable\n")
        return
    try:
        content = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        sys.stdout.write("Sign-in diagnostic markers unavailable\n")
        return
    if not content:
        sys.stdout.write("Sign-in diagnostic markers unavailable\n")
        return
    lines = content.splitlines()
    if len(lines) > 100 or any(not marker.fullmatch(line) for line in lines):
        sys.stdout.write("Sign-in diagnostic markers invalid\n")
        return
    for line in lines:
        sys.stdout.write(line + "\n")


def main():
    """Verify the actual checkout without printing its request parameters."""
    result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    if result.returncode or not authorized(os.environ, result.stdout.strip(), platform.system(), platform.machine()):
        sys.stderr.write("Sign-in diagnostic authorization refused\n")
        return 1
    if len(sys.argv) == 3 and sys.argv[1] == "--report":
        report(sys.argv[2])
    elif len(sys.argv) == 1:
        sys.stdout.write("Sign-in diagnostic authorization accepted\n")
    else:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
