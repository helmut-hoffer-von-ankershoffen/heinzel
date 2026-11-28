# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///
"""Validate the hosted chain's pre-merge receipt before deferring the GitHub Latest designation."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

# The checkout has already been pinned by the caller. Load its exact sibling even under Python -I.
CHAIN_SPEC = importlib.util.spec_from_file_location(
    "_release_latest_chain", Path(__file__).with_name("release_chain.py")
)
if CHAIN_SPEC is None or CHAIN_SPEC.loader is None:
    msg = "cannot load the checked-out Core release chain"
    raise ImportError(msg)
chain = importlib.util.module_from_spec(CHAIN_SPEC)
sys.modules[CHAIN_SPEC.name] = chain
CHAIN_SPEC.loader.exec_module(chain)
DEFER_LATEST_TRAILER, REPOSITORY, SHA, VERSION = chain.DEFER_LATEST_TRAILER, chain.REPOSITORY, chain.SHA, chain.VERSION
ChainError, get, run = chain.ChainError, chain.get, chain.run

RECEIPT = "release-defer-latest"
WORKFLOW = ".github/workflows/release-chain.yml"
RECEIPT_LIMIT = 2048


def chain_run_in(message: str) -> int | None:
    """Read one exact reserved trailer, or recognize a legacy release.

    Raises:
        ChainError: A reserved trailer is present in an invalid or ambiguous form.
    """
    lines = [line for line in message.splitlines() if line.strip()]
    found = [line for line in lines if line.lstrip().casefold().startswith(DEFER_LATEST_TRAILER.casefold())]
    if not found:
        return None
    match = re.fullmatch(rf"{DEFER_LATEST_TRAILER}: ([1-9][0-9]*)", found[0])
    if len(found) != 1 or found[0] != lines[-1] or match is None:
        msg = "malformed or duplicate release-tail trailer on the protected commit"
        raise ChainError(msg)
    return int(match[1])


def official_chain(read: Callable[[str], Any], chain_run: int) -> None:
    """Require the first attempt of the official protected-main release chain.

    Raises:
        ChainError: The run does not match the official invocation.
    """
    workflow = read(f"actions/runs/{chain_run}/attempts/1") or {}
    if (
        not isinstance(workflow, dict)
        or (workflow.get("id"), workflow.get("run_attempt"), workflow.get("path"), workflow.get("head_branch"))
        != (chain_run, 1, WORKFLOW, "main")
        or workflow.get("event") not in {"workflow_dispatch", "schedule"}
        or (workflow.get("repository") or {}).get("full_name") != REPOSITORY
        or (workflow.get("head_repository") or {}).get("full_name") != REPOSITORY
    ):
        msg = "defer-Latest receipt does not belong to the official release chain on main"
        raise ChainError(msg)


def receipt_for(chain_run: int) -> dict[str, Any]:
    """Download only the named immutable artifact from the identified GitHub run.

    Raises:
        ChainError: The downloaded receipt cannot be read.
    """
    name = f"{RECEIPT}-1"
    data = get(f"actions/runs/{chain_run}/artifacts?name={name}&per_page=100") or {}
    artifacts = data.get("artifacts", [])
    if (
        data.get("total_count") != 1
        or len(artifacts) != 1
        or artifacts[0].get("name") != name
        or artifacts[0].get("expired") is not False
        or (artifacts[0].get("workflow_run") or {}).get("id") != chain_run
    ):
        msg = "missing, expired or ambiguous same-run defer-Latest artifact"
        raise ChainError(msg)
    with tempfile.TemporaryDirectory() as folder:
        run(["gh", "run", "download", str(chain_run), "-R", REPOSITORY, "-n", name, "-D", folder])
        path = Path(folder) / f"{RECEIPT}.json"
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= RECEIPT_LIMIT:
            msg = "missing or unreadable defer-Latest receipt"
            raise ChainError(msg)
        return json.loads(path.read_text(encoding="utf-8"))


def commit_identity(commit: dict[str, Any], sha: str, tree: str) -> str:
    """Return the validated sole parent of the given exact GitHub commit and tree.

    Raises:
        ChainError: The commit, tree or parent does not match the required identity.
    """
    parents = commit.get("parents", [])
    if (
        commit.get("sha") != sha
        or (commit.get("commit") or {}).get("tree", {}).get("sha") != tree
        or not isinstance(parents, list)
        or len(parents) != 1
        or not SHA.fullmatch(str(parents[0].get("sha", "")))
    ):
        msg = "the defer-Latest receipt differs from the exact commit tree or parent"
        raise ChainError(msg)
    return str(parents[0]["sha"])


def merged_release_head(tag: str, sha: str, read: Callable[[str], Any]) -> tuple[int, str]:
    """Resolve the unique merged release PR and its exact candidate head from the tag commit.

    Raises:
        ChainError: The tag cannot be bound to an unambiguous merged release PR.
    """
    if not tag.startswith("v") or not VERSION.fullmatch(tag[1:]) or not SHA.fullmatch(sha):
        msg = "invalid release tag or exact commit"
        raise ChainError(msg)
    rows = read(f"commits/{sha}/pulls?per_page=100")
    if (
        not isinstance(rows, list)
        or len(rows) != 1
        or not isinstance(rows[0], dict)
        or not rows[0].get("merged_at")
        or rows[0].get("merge_commit_sha") != sha
        or type(rows[0].get("number")) is not int
        or rows[0]["number"] < 1
    ):
        msg = "the tag commit has no unique merged release PR"
        raise ChainError(msg)
    number = rows[0]["number"]
    head = str((rows[0].get("head") or {}).get("sha", ""))
    pr = read(f"pulls/{number}") or {}
    source, base = pr.get("head", {}), pr.get("base", {})
    if (
        not SHA.fullmatch(head)
        or (pr.get("number"), pr.get("title"), pr.get("state"), pr.get("merged"), pr.get("merge_commit_sha"))
        != (number, f"chore(release): {tag}", "closed", True, sha)
        or (source.get("sha"), source.get("ref"), (source.get("repo") or {}).get("full_name"))
        != (head, f"release/{tag}", REPOSITORY)
        or (base.get("ref"), (base.get("repo") or {}).get("full_name")) != ("main", REPOSITORY)
    ):
        msg = "the tagged squash is not bound to the exact merged release PR head"
        raise ChainError(msg)
    return number, head


def defer_latest(
    tag: str, sha: str, message: str, read: Callable[[str], Any], read_receipt: Callable[[int], dict[str, Any]]
) -> bool:
    """Only a protected merge matching the official pre-merge receipt may opt into delayed Latest.

    Raises:
        ChainError: The release cannot be classified, or the chain evidence does not bind this merge.
    """
    chain_run = chain_run_in(message)
    number, head = merged_release_head(tag, sha, read)
    source = read(f"commits/{head}") or {}
    candidate_message = (source.get("commit") or {}).get("message")
    if source.get("sha") != head or not isinstance(candidate_message, str):
        msg = "the exact merged release candidate message is unreadable"
        raise ChainError(msg)
    marked = chain.tail_required(read, head)
    if chain_run is None:
        if not marked and chain.DEFER_LATEST_CANDIDATE not in candidate_message.splitlines():
            return False
        msg = "this chain candidate has no verified defer-Latest merge trailer"
        raise ChainError(msg)
    if not marked:
        msg = "the release-tail trailer has no durable status on the exact candidate head"
        raise ChainError(msg)
    official_chain(read, chain_run)
    proof = read_receipt(chain_run)
    if (
        not isinstance(proof, dict)
        or (proof.get("schema"), proof.get("version"), proof.get("chain_run"), proof.get("pr"), proof.get("head"))
        != (1, tag[1:], chain_run, number, head)
        or type(proof.get("candidate_run")) is not int
        or proof["candidate_run"] < 1
        or not SHA.fullmatch(str(proof.get("tree", "")))
    ):
        msg = "invalid defer-Latest receipt for this release version, candidate or chain run"
        raise ChainError(msg)
    tree = proof["tree"]
    if message.splitlines()[0] != f"chore(release): {tag} (#{number})" or commit_identity(
        source, head, tree
    ) != commit_identity(read(f"commits/{sha}") or {}, sha, tree):
        msg = "the defer-Latest receipt does not bind this protected release squash merge"
        raise ChainError(msg)
    return True


def signing_candidate(tag: str, sha: str, message: str, *, read: Callable[[str], Any], configured_run: str) -> dict:
    """Verify the selected original candidate against the merged PR before releasing signing authority."""
    number, head = merged_release_head(tag, sha, read)
    squash = read(f"commits/{sha}") or {}
    tree = (squash.get("commit") or {}).get("tree", {}).get("sha", "")
    if not isinstance(tree, str) or not SHA.fullmatch(tree):
        msg = "the release squash has no valid tree"
        raise ChainError(msg)
    parent = commit_identity(squash, sha, tree)
    if commit_identity(read(f"commits/{head}") or {}, head, tree) != parent:
        msg = "release squash and approved PR head must have identical trees and parents"
        raise ChainError(msg)

    chain_run = chain_run_in(message)
    # Read the authenticated receipt once. defer_latest verifies its identity/status against this exact squash.
    proof = receipt_for(chain_run) if chain_run is not None else {}
    defer_latest(tag, sha, message, read, lambda _: proof)
    selected = str(proof.get("candidate_run", "")) if chain_run is not None else configured_run
    if not re.fullmatch(r"[1-9][0-9]{0,18}", selected):
        msg = "no original candidate run was pinned for this protected release"
        raise ChainError(msg)

    candidate = chain.load_candidate_module()
    pins = {
        path: hashlib.sha256(candidate.source_bytes(read, path, sha)).hexdigest()
        for path in (candidate.WORKFLOW, candidate.RECORDER)
    }
    evidence = candidate.verify(
        read,
        chain.download_artifact,
        candidate.ExpectedCandidate(int(selected), number, head, tree, merged=(sha, parent)),
        pins,
    )
    merged_at = (read(f"pulls/{number}") or {}).get("merged_at")
    if not isinstance(merged_at, str) or datetime.fromisoformat(evidence["updated_at"]) > datetime.fromisoformat(
        merged_at
    ):
        msg = "the candidate was not complete before the protected PR merged"
        raise ChainError(msg)
    return evidence


def main(tag: str, sha: str, mode: str = "") -> int:
    """Print the producer mode after the caller verifies the exact tag and protected main ancestry."""
    try:
        message = run(["git", "show", "-s", "--format=%B", sha])
        if mode == "--signing":
            evidence = signing_candidate(
                tag, sha, message, read=get, configured_run=os.environ.get("HEINZEL_MAC_CANDIDATE_RUN", "")
            )
            if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
                with Path(summary).open("a", encoding="utf-8") as output:
                    output.write(
                        f"Approved candidate run {evidence['run_id']} attempt 1, {evidence['job_count']} jobs, "
                        f"head {evidence['head']}, tested checkout {evidence['source_commit']}, "
                        f"tree {evidence['tree']}, protected squash {sha}.\n"
                    )
            print(
                f"candidate_run={evidence['run_id']}\ncandidate_checkout={evidence['source_commit']}\ncandidate_head={evidence['head']}"
            )
            return 0
        if mode:
            return 2
        defer = defer_latest(tag, sha, message, get, receipt_for)
    except (ChainError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        sys.stderr.write(f"release Latest policy refused: {exc}\n")
        return 1
    sys.stdout.write("defer_latest=" + str(defer).lower() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
