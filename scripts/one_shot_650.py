# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""One-shot fast-forward draft; queue/train and all base writers must be held externally."""

import json
import os
import subprocess
import sys
from typing import Any

REPO = "helmut-hoffer-von-ankershoffen/heinzel"
BASE = "b234067b172f33db59f98c9ea38808b3d5ab9bc5"
OLD = "da281f90d4d1abeffd38a3d7430817e21cfa3d4a"
NEW = "37ea58445ac9ac73f9b698de3bd852a2821e8501"
BRANCH = "fix/five-flakes"


def api(path: str, *args: str) -> Any:  # noqa: ANN401
    """Call GitHub using fixed repository and argument lists."""
    cmd = ["gh", "api", f"repos/{REPO}/{path}", *args]
    return json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)  # noqa: S603


def snapshot(expected: str) -> dict[str, Any]:
    """Refuse stale source or PR state.

    Raises:
        ValueError: The expected source or PR state changed.
    """
    main = api("git/ref/heads/main")["object"]["sha"]
    ref = api(f"git/ref/heads/{BRANCH}")["object"]["sha"]
    pr = api("pulls/650")
    if main != BASE or ref != expected or pr["head"]["sha"] != expected or pr["head"]["ref"] != BRANCH:
        msg = "base or PR head drift; stop"
        raise ValueError(msg)
    if pr["state"] != "open" or pr["draft"] or pr["base"]["ref"] != "main":
        msg = "PR state drift; stop"
        raise ValueError(msg)
    return pr


def main() -> None:
    """Guard and request one non-force update.

    Raises:
        ValueError: An execution or source precondition failed.
    """
    if (
        os.environ.get("GITHUB_REPOSITORY") != REPO
        or os.environ.get("GITHUB_RUN_ATTEMPT") != "1"
        or os.environ.get("GITHUB_REF") != "refs/heads/bootstrap/one-shot-650-update"
        or os.environ.get("GITHUB_ACTOR") != "helmut-hoffer-von-ankershoffen"
        or not os.environ.get("REVIEWED_HELPER_SHA")
        or os.environ.get("GITHUB_SHA") != os.environ["REVIEWED_HELPER_SHA"]
    ):
        msg = "unreviewed helper or unexpected execution context"
        raise ValueError(msg)
    msg = "ref update held until writer serialization is independently enforced"
    raise ValueError(msg)
    snapshot(OLD)
    comparison = api(f"compare/{OLD}...{NEW}")
    if comparison.get("status") != "ahead" or comparison.get("base_commit", {}).get("sha") != OLD:
        msg = "candidate is not a fast-forward of the pinned old head"
        raise ValueError(msg)
    # No expected-old CAS exists here. External serialization and the adjacent reads are required.
    snapshot(OLD)
    response = api(f"git/refs/heads/{BRANCH}", "--method", "PATCH", "-f", f"sha={NEW}", "-F", "force=false")
    if response.get("object", {}).get("sha") != NEW:
        msg = "GitHub did not confirm target ref; inspect actual state"
        raise ValueError(msg)
    snapshot(NEW)  # PR API lag or any drift stops; a write may already have happened.
    print(f"Observed fast-forward {OLD} -> {NEW}, main {BASE}; independently verify test merge")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Stopped; inspect GitHub state: {exc}", file=sys.stderr)
        sys.exit(1)
