"""Admit one reviewed test-only rehearsal from the protected base checkout."""

import hashlib
import os
import sys

from focused_ci_collect import DIGEST, git_bytes, require, sha

TEST = "tests/unit/focused_ci_receipt_test.py"


def check_scope(pr: str, base: str, head: str, merge: str, digest: str) -> None:
    """Require the approved exact Git diff and GitHub merge tree before any PR code is run."""
    require(pr.isascii() and pr.isdecimal() and int(pr) > 0, "Invalid rehearsal PR number")
    commits = (base, head, merge)
    require(all(sha(item) for item in commits) and len(set(commits)) == len(commits), "Invalid rehearsal SHAs")
    require(bool(DIGEST.fullmatch(digest)), "Missing approved rehearsal diff digest")
    require(git_bytes("rev-parse", "HEAD").decode().strip() == base, "Rehearsal scope must run from the approved base")
    head_ref, merge_ref = f"refs/pull/{pr}/head", f"refs/pull/{pr}/merge"
    refs = git_bytes("ls-remote", "--exit-code", "origin", "refs/heads/main", head_ref, merge_ref).decode().splitlines()
    require(f"{base}\trefs/heads/main" in refs, "Protected main moved since rehearsal approval")
    require(f"{head}\t{head_ref}" in refs and f"{merge}\t{merge_ref}" in refs, "The rehearsal head or merge ref moved")
    git_bytes("fetch", "--no-tags", "--no-recurse-submodules", "origin", head_ref, merge_ref)
    git_bytes("merge-base", "--is-ancestor", base, head)
    require(
        git_bytes("rev-list", "--parents", "-n", "1", merge).decode().split() == [merge, base, head]
        and git_bytes("rev-parse", f"{merge}^{{tree}}") == git_bytes("rev-parse", f"{head}^{{tree}}"),
        "Unexpected rehearsal merge parents or tree",
    )
    paths = git_bytes("diff-tree", "--no-commit-id", "-r", "--name-status", "--no-renames", "-z", base, head)
    require(paths == f"M\0{TEST}\0".encode(), "Rehearsal must modify only the receipt test file")
    diff = git_bytes("diff-tree", "--no-commit-id", "-r", "--raw", "--no-abbrev", "--no-renames", "-z", base, head)
    require(hashlib.sha256(diff).hexdigest() == digest, "Rehearsal diff does not match approval")


def main() -> int:
    """Read the administrator pins and GitHub's merge SHA."""
    try:
        check_scope(
            os.environ["FOCUSED_REHEARSAL_PR"],
            os.environ["FOCUSED_REHEARSAL_BASE"],
            os.environ["FOCUSED_REHEARSAL_HEAD"],
            os.environ["GITHUB_SHA"],
            os.environ["FOCUSED_REHEARSAL_DIFF_SHA256"],
        )
    except (KeyError, ValueError) as error:
        print(f"::error::Focused rehearsal refused: {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
