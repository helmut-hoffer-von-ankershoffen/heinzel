# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///
"""The Core release chain in GitHub Actions (workflow "Release"), one step per command.

Every step reads GitHub state first, so a new run of the workflow continues where the last one stopped, and the
release completes with no machine of ours awake.

Order and rules (they are the rules the local driver kept):
- plan: the version, and what is done already. A cut needs main's tip green on the FIRST attempt of its push run
  (no known-flake exception: CI cannot read beads), or while that run is not finished the first-attempt green run of
  the merged PR whose head has the same tree; no red macOS lane on that tip, the previous release terminal (its
  `acceptance.json` exists), releasable commits since the last tag. The intro, unless given, is drafted from the
  same commits the notes list.
- scope: the release commit is one commit on that tip and changes only the release files.
- notes: the new CHANGELOG.md section names every commit since the last tag (a combine by all its members).
- freeze: while a release PR is open, the check "Release freeze" fails on every other open PR.
- candidate: the release PR's one CI run, first attempt, full job inventory (scripts/release_candidate.py).
- merge: only when main has not moved since the candidate base and every check main's rules require has passed on
  the head (the merge state is never read); squash, pinned to the verified head.
- compose: dispatched once the tag's wheel asset answers, beside PyPI.
- snapshot: after the GitHub release, PyPI and the composed app exist: the wheel and profile digests, with the
  GitHub and PyPI wheel equal.
- verdict: both acceptance receipts of this run's first attempt prove those digests; then the marker; then Latest.
  Each receipt is the reduction of the sharded acceptance (acceptance.yml, every shard of its OS, same attempt).
- dry run: plan (optionally pinned to a given cut SHA on main) and the candidate verdict of that version's release
  PR, read-only: nothing is pushed, merged or published (`plan --dry-run`, `candidate --dry-run`).
- probe (dry run with probe, from main only): the owner's token on a throwaway branch and draft PR, removed again.

Usage: uv run --no-project --frozen --no-build --python 3.15 python scripts/release_chain.py COMMAND ...
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

REPOSITORY = "helmut-hoffer-von-ankershoffen/heinzel"
PLUGINS = "helmut-hoffer-von-ankershoffen/heinzel-plugins"
CI = "ci.yml"
MARKER = "acceptance.json"
FREEZE_CHECK = "Release freeze"
ACCEPTED = ("ACCEPTED", "ACCEPTED_EXCEPT_UNPROVABLE")
RECEIPT_SCHEMA = 2  # the sharded acceptance reduction (scripts/acceptance/reduce.py --release)
RECEIPTS = {"Linux": "acceptance-result-Linux", "macOS": "acceptance-result-macOS"}
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
RELEASE_SCOPE = frozenset(
    {
        "CHANGELOG.md",
        "agent-skills/.claude-plugin/plugin.json",
        "copier.yml",
        "pyproject.toml",
        "scripts/acceptance/run_acceptance.py",
        "uv.lock",
    }
)
NOT_RELEASABLE = re.compile(r"^(chore\(release\)|docs|test|ci|style)(\([^)]*\))?!?:")
USER_FACING = re.compile(r"^(feat|fix|perf|revert)(\([^)]*\))?!?:\s*")
COMBINES = re.compile(r"^Combines #(?P<number>\d+) (?P<subject>\S.*)$")
MEMBERS = re.compile(r"^(?:Combines #(\d+)\b.*|Merged via this PR:\s*(.*))$", re.MULTILINE)
RED = ("failure", "timed_out", "startup_failure")
TAIL = "release-tail.yml"
MAX_IN_FLIGHT = 2  # releases at once: one in its tail, one at its front (cut, candidate, merge, tag)
PASSING = ("success", "neutral", "skipped")
PROBE_BRANCH = "release-probe/"
INTRO_ITEMS = 4
INTRO_LIMIT = 600
PAGE = 100
POLL = 20
COMMAND_TIMEOUT = 300  # seconds for one gh, git or curl call
RUN_OUTPUT = "GITHUB_OUTPUT"
NO_RELEASE_IDENTITY = (
    "the repository secret RELEASE_TOKEN is missing: the release PR is opened and merged as the owner identity. Create "
    "a fine-grained token of the owner for helmut-hoffer-von-ankershoffen/heinzel with Contents, Pull requests and "
    "Actions read and write (and Actions read and write on heinzel-plugins, to dispatch compose), then "
    "`gh secret set RELEASE_TOKEN -R helmut-hoffer-von-ankershoffen/heinzel`"
)


class ChainError(RuntimeError):
    """A step refused: the text says why and what to do; nothing after it ran."""


class Wait(ChainError):  # noqa: N818 - a state, not a failure: the step is not done yet
    """The step is not done yet; poll again."""


def say(text: str) -> None:
    """One line of the run log."""
    print(text, flush=True)


def run(argv: list[str], *, env: dict[str, str] | None = None) -> str:
    """Run a command and return its stdout.

    Raises:
        ChainError: The command failed; its stderr tail is in the message.
    """
    try:
        done = subprocess.run(argv, capture_output=True, text=True, check=False, env=env, timeout=COMMAND_TIMEOUT)  # noqa: S603
    except subprocess.TimeoutExpired as exc:
        msg = f"{argv[0]} {argv[1] if len(argv) > 1 else ''} timed out after {COMMAND_TIMEOUT} s"
        raise ChainError(msg) from exc
    if done.returncode:
        tail = (done.stderr or done.stdout).strip()[-600:]
        msg = f"{argv[0]} {argv[1] if len(argv) > 1 else ''} failed ({done.returncode}): {tail}"
        raise ChainError(msg)
    return done.stdout


def gh_env(token: str | None = None) -> dict[str, str]:
    """The environment for a gh call, with an explicit token when the step acts as the release identity."""
    env = dict(os.environ)
    if token:
        env["GH_TOKEN"] = token
    return env


def get(path: str, *, repo: str = REPOSITORY) -> Any:  # noqa: ANN401 - GitHub JSON, validated by each caller
    """GET one REST path of `repo`; a 404 is None.

    Raises:
        ChainError: Any other failure.
    """
    try:
        done = subprocess.run(  # noqa: S603
            ["gh", "api", f"repos/{repo}/{path}"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"GitHub read {path} timed out after {COMMAND_TIMEOUT} s"
        raise ChainError(msg) from exc
    if done.returncode:
        if "HTTP 404" in done.stderr or "Not Found" in done.stderr:
            return None
        msg = f"GitHub read {path} failed: {done.stderr.strip()[-300:]}"
        raise ChainError(msg)
    return json.loads(done.stdout)


def vkey(version: str) -> tuple[int, int, int]:
    """Sort key of an exact version.

    Raises:
        ChainError: `version` is not X.Y.Z.
    """
    match = VERSION.fullmatch(version)
    if not match:
        msg = f"{version!r} is no exact version X.Y.Z"
        raise ChainError(msg)
    return int(match[1]), int(match[2]), int(match[3])


def bump_kind(current: str, wanted: str) -> str:
    """The release.sh bump that takes `current` to `wanted`.

    Raises:
        ChainError: `wanted` is not the next patch, minor or major.
    """
    major, minor, patch = vkey(current)
    kinds = {
        f"{major}.{minor}.{patch + 1}": "patch",
        f"{major}.{minor + 1}.0": "minor",
        f"{major + 1}.0.0": "major",
    }
    vkey(wanted)
    if wanted not in kinds:
        msg = f"{wanted} is not the next patch, minor or major after {current}"
        raise ChainError(msg)
    return kinds[wanted]


# ------------------------------------------------------------------------------------------------ reading GitHub


def releases(read: Callable[[str], Any]) -> list[dict[str, Any]]:
    """The newest Core releases (one page is enough: the chain only looks at the last few).

    Raises:
        ChainError: The listing is unreadable.
    """
    found = read(f"releases?per_page={PAGE}")
    if not isinstance(found, list):
        msg = "cannot read the Core releases"
        raise ChainError(msg)
    return found


def newest_final(read: Callable[[str], Any]) -> str:
    """The newest published final vX.Y.Z, without its v.

    Raises:
        ChainError: There is none.
    """
    tags = [
        r["tag_name"][1:]
        for r in releases(read)
        if r.get("draft") is False
        and r.get("prerelease") is False
        and isinstance(r.get("tag_name"), str)
        and r["tag_name"].startswith("v")
        and VERSION.fullmatch(r["tag_name"][1:])
    ]
    if not tags:
        msg = "no published final Core release"
        raise ChainError(msg)
    return max(tags, key=vkey)


def release_of(read: Callable[[str], Any], version: str) -> dict[str, Any] | None:
    """The published (non-draft) GitHub release vX.Y.Z, else None."""
    release = read(f"releases/tags/v{version}")
    if not isinstance(release, dict) or release.get("tag_name") != f"v{version}" or release.get("draft") is not False:
        return None
    return release


def asset_digest(release: dict[str, Any], name: str) -> str | None:
    """The provider sha256 of the one asset `name`, else None."""
    assets = [a for a in release.get("assets", []) if a.get("name") == name]
    if len(assets) != 1:
        return None
    digest = assets[0].get("digest", "")
    return (
        digest[7:]
        if isinstance(digest, str) and digest.startswith("sha256:") and DIGEST.fullmatch(digest[7:])
        else None
    )


def has_marker(release: dict[str, Any] | None) -> bool:
    """Whether the release carries its acceptance marker (the release is terminal)."""
    return bool(release) and any(a.get("name") == MARKER for a in release.get("assets", []))  # type: ignore[union-attr]


def open_release_prs(read: Callable[[str], Any]) -> list[dict[str, Any]]:
    """Open, same-repository release PRs (head release/vX.Y.Z, title chore(release): vX.Y.Z)."""
    found = []
    for pr in open_prs(read):
        ref = pr.get("head", {}).get("ref", "")
        version = ref.removeprefix("release/v")
        if (
            ref.startswith("release/v")
            and VERSION.fullmatch(version)
            and pr.get("head", {}).get("repo", {}).get("full_name") == REPOSITORY
            and pr.get("title") == f"chore(release): v{version}"
        ):
            found.append(pr)
    return found


def open_prs(read: Callable[[str], Any]) -> list[dict[str, Any]]:
    """Every open PR against main.

    Raises:
        ChainError: The listing is unreadable or too long to read whole.
    """
    found: list[dict[str, Any]] = []
    for page in range(1, 11):
        rows = read(f"pulls?state=open&base=main&per_page={PAGE}&page={page}")
        if not isinstance(rows, list):
            msg = "cannot read the open pull requests"
            raise ChainError(msg)
        found += rows
        if len(rows) < PAGE:
            return found
    msg = "more than 1000 open pull requests; the freeze cannot cover them"
    raise ChainError(msg)


def ci_runs(read: Callable[[str], Any], sha: str) -> list[dict[str, Any]]:
    """Every CI run of commit `sha`.

    Raises:
        ChainError: The listing is unreadable.
    """
    data = read(f"actions/workflows/{CI}/runs?head_sha={sha}&per_page={PAGE}")
    if not isinstance(data, dict) or not isinstance(data.get("workflow_runs"), list):
        msg = f"cannot read the CI runs of {sha[:9]}"
        raise ChainError(msg)
    return data["workflow_runs"]


def tree_of(read: Callable[[str], Any], sha: str) -> str:
    """The git tree of commit `sha`, or empty when unreadable."""
    data = read(f"commits/{sha}")
    tree = (data or {}).get("commit", {}).get("tree", {}).get("sha", "") if isinstance(data, dict) else ""
    return str(tree) if SHA.fullmatch(str(tree)) else ""


def same_tree_proof(read: Callable[[str], Any], tip: str) -> str | None:
    """The PR run that proves `tip` by tree identity, else None.

    `tip` is the merge of exactly one PR into main whose head has the same git tree (a squash of an up-to-date head),
    and every CI run of that head is complete and green on its first attempt: that run tested exactly main's tree.
    """
    prs = read(f"commits/{tip}/pulls")
    merged = [
        p
        for p in (prs if isinstance(prs, list) else [])
        if isinstance(p, dict) and p.get("merged_at") and p.get("base", {}).get("ref") == "main"
    ]
    if len(merged) != 1:
        return None
    head = str(merged[0].get("head", {}).get("sha", ""))
    tree = tree_of(read, tip)
    if not SHA.fullmatch(head) or not tree or tree_of(read, head) != tree:
        return None
    runs = [r for r in ci_runs(read, head) if r.get("event") == "pull_request" and r.get("head_sha") == head]
    if not runs or any(
        r.get("status") != "completed" or r.get("conclusion") != "success" or r.get("run_attempt") != 1 for r in runs
    ):
        return None
    run_id = max(int(r.get("id", 0)) for r in runs)
    return f"PR #{merged[0].get('number')} run {run_id} on {head[:9]}, the same tree as main tip {tip[:9]}"


def main_gate(read: Callable[[str], Any], tip: str) -> str:
    """Main's tip may be cut: it is proven green on a first attempt and no macOS lane run of it is red.

    The proof is its push run, green on attempt 1; or, while that run is missing or running, the first-attempt green
    run of the merged PR whose head has main's tree (main's own run then only confirms). A finished red push run
    always refuses.

    Returns:
        Which run proves the tip.

    Raises:
        Wait: Neither proof exists yet.
        ChainError: The push run is red, green only on a rerun or ambiguous; or a macOS lane run is red.
    """
    rows = ci_runs(read, tip)
    pushed = [r for r in rows if r.get("event") == "push"]
    if len(pushed) > 1:
        msg = f"main tip {tip[:9]} has {len(pushed)} CI push runs; refusing to pick one"
        raise ChainError(msg)
    push = pushed[0] if pushed else None
    where = f"main tip {tip[:9]} (run {push.get('id')})" if push else f"main tip {tip[:9]}"
    if push and push.get("status") == "completed":
        if push.get("conclusion") != "success":
            msg = f"{where}: CI ended {push.get('conclusion')}; fix main first"
            raise ChainError(msg)
        if push.get("run_attempt") != 1:
            msg = f"{where}: green only on attempt {push.get('run_attempt')}; a release needs a first-attempt green run"
            raise ChainError(msg)
        proof = f"push run {push.get('id')} of main tip {tip[:9]}, first attempt"
    elif not (proof := same_tree_proof(read, tip) or ""):
        msg = f"{where}: CI is {push.get('status')}" if push else f"main tip {tip[:9]} has no CI push run yet"
        raise Wait(msg)
    for lane in rows:
        if (
            lane.get("event") == "workflow_dispatch"
            and lane.get("status") == "completed"
            and lane.get("conclusion") in RED
        ):
            msg = f"main tip {tip[:9]}: the macOS lane run {lane.get('id')} is {lane.get('conclusion')}; fix main first"
            raise ChainError(msg)
    return proof


def compare_commits(read: Callable[[str], Any], base: str, head: str) -> list[dict[str, Any]]:
    """The commits from `base` (a tag) to `head`, oldest first.

    Raises:
        ChainError: The comparison is unreadable or longer than one page of 250.
    """
    data = read(f"compare/{base}...{head}?per_page=250")
    if not isinstance(data, dict) or not isinstance(data.get("commits"), list):
        msg = f"cannot compare {base} with {head[:9]}"
        raise ChainError(msg)
    if data.get("total_commits", 0) > len(data["commits"]):
        msg = f"more than {len(data['commits'])} commits since {base}; cut by hand"
        raise ChainError(msg)
    return data["commits"]


def commit_subjects(message: str) -> list[str]:
    """A commit's subject, or for a combine squash commit its members' subjects (wrapped lines joined)."""
    members: list[str] = []
    current = None
    for raw in message.splitlines()[1:]:
        line = raw.strip()
        if match := COMBINES.match(line):
            current = len(members)
            members.append(match["subject"])
        elif current is not None and line and not line.startswith(("Combines", "<!--")):
            members[current] += " " + line
        else:
            current = None
    return members or message.splitlines()[:1]


def intro_from(subjects: list[str]) -> str:
    """The first user-facing changes in the order given, plus how many more there are."""
    ranked = [s for s in subjects if USER_FACING.match(s)]
    items = [re.sub(r"\s*\(#\d+\)$", "", USER_FACING.sub("", s)) for s in ranked[:INTRO_ITEMS]]
    items = [i[:1].upper() + i[1:] for i in items]
    if not items:
        return "Reliability and maintenance fixes."
    more = len(ranked) - len(items)
    text = "; ".join(items) + (f"; and {more} more" if more else "")
    return (text[: INTRO_LIMIT - 3] + "...") if len(text) > INTRO_LIMIT else text + "."


# ------------------------------------------------------------------------------------------------ plan


def step_plan(version: str, phase: str, reason: str, **extra: str) -> dict[str, str]:
    """One plan result; the fields a phase does not use are empty."""
    empty = dict.fromkeys(("pr", "bump", "base", "intro", "previous"), "")
    return {"version": version, "phase": phase, "reason": reason} | empty | extra


def final_below(read: Callable[[str], Any], version: str) -> str:
    """The newest published final release older than `version`: the previous release of a pinned (re)plan.

    Raises:
        ChainError: There is none.
    """
    tags = [
        r["tag_name"][1:]
        for r in releases(read)
        if r.get("draft") is False
        and r.get("prerelease") is False
        and isinstance(r.get("tag_name"), str)
        and r["tag_name"].startswith("v")
        and VERSION.fullmatch(r["tag_name"][1:])
        and vkey(r["tag_name"][1:]) < vkey(version)
    ]
    if not tags:
        msg = f"no published final Core release before {version}"
        raise ChainError(msg)
    return max(tags, key=vkey)


def on_main(read: Callable[[str], Any], sha: str) -> None:
    """`sha` is a commit of main (main's tip or behind it).

    Raises:
        ChainError: It is not a full SHA, or main does not contain it.
    """
    if not SHA.fullmatch(sha):
        msg = f"{sha!r} is not a full commit SHA"
        raise ChainError(msg)
    data = read(f"compare/{sha}...main")
    if not isinstance(data, dict) or data.get("status") not in ("ahead", "identical"):
        msg = f"{sha[:9]} is not a commit of main"
        raise ChainError(msg)


def in_tail(read: Callable[[str], Any]) -> list[str]:
    """Published final releases without their acceptance marker, newer than the newest terminal one, oldest first.

    These are in their tail (PyPI, compose, acceptance, verdict, Latest), or their tail run stopped.
    """
    finals = [
        r
        for r in releases(read)
        if r.get("draft") is False
        and r.get("prerelease") is False
        and isinstance(r.get("tag_name"), str)
        and r["tag_name"].startswith("v")
        and VERSION.fullmatch(r["tag_name"][1:])
    ]
    terminal = [vkey(r["tag_name"][1:]) for r in finals if has_marker(r)]
    floor = max(terminal) if terminal else (-1, -1, -1)
    return sorted(
        (r["tag_name"][1:] for r in finals if not has_marker(r) and vkey(r["tag_name"][1:]) > floor), key=vkey
    )


def tail_running(read: Callable[[str], Any], version: str) -> bool:
    """Whether a run of the tail workflow for `version` is queued or running (its run-name names the version).

    Raises:
        ChainError: The listing is unreadable.
    """
    data = read(f"actions/workflows/{TAIL}/runs?per_page={PAGE}")
    if data is None:  # the workflow is not on the default branch yet: no run of it exists
        return False
    if not isinstance(data, dict) or not isinstance(data.get("workflow_runs"), list):
        msg = "cannot read the release tail runs"
        raise ChainError(msg)
    return any(
        r.get("status") != "completed" and str(r.get("display_title", "")).startswith(f"Release tail v{version}")
        for r in data["workflow_runs"]
    )


def in_flight(read: Callable[[str], Any], requested: str, current: str) -> dict[str, str] | None:
    """The release whose front is under way (an open release PR), or one in its tail with no tail run, else None.

    A release whose tail is running is not in the way: the next release may be cut and run its candidate meanwhile
    (pipelining, at most two in flight); it waits only at its own verdict for the older one to be terminal.

    Raises:
        ChainError: More than one release PR is open, or `requested` is another version than the one in flight.
    """
    prs = open_release_prs(read)
    if len(prs) > 1:
        msg = "more than one open release PR: " + ", ".join(f"#{p['number']}" for p in prs) + "; close all but one"
        raise ChainError(msg)
    found = None
    if prs:
        number = str(prs[0]["number"])
        version = prs[0]["head"]["ref"].removeprefix("release/v")
        found = step_plan(version, "candidate", f"resume: release PR #{number} is open", pr=number)
    else:
        for version in in_tail(read):
            if vkey(version) <= vkey(current) and not tail_running(read, version):
                found = step_plan(version, "tail", f"resume: v{version} is published and no tail run of it is running")
                break
    if found and requested and requested != found["version"]:
        msg = f"v{found['version']} is in flight ({found['reason']}); finish it before {requested}"
        raise ChainError(msg)
    return found


def check_unpublished_merge(read: Callable[[str], Any], commits: list[dict[str, Any]], current: str) -> None:
    """Refuse a new cut while main contains an unresolved unpublished release merge.

    Raises:
        ChainError: The release is awaiting publication, or its source cannot be proved uniquely.
    """
    candidates = []
    for commit in commits:
        subject = str(commit.get("commit", {}).get("message", "")).partition("\n")[0]
        match = re.fullmatch(r"chore\(release\): v([0-9.]+)(?: \(#([1-9][0-9]*)\))?", subject)
        if match and VERSION.fullmatch(match[1]) and vkey(match[1]) > vkey(current):
            candidates.append((match[1], match[2], str(commit.get("sha", ""))))
    if not candidates:
        return
    if len(candidates) != 1 or not candidates[0][1]:
        msg = "ambiguous unpublished release history on main; reconcile it before cutting"
        raise ChainError(msg)

    version, number, sha = candidates[0]
    pr = pr_of(read, int(number), version)
    base = pr.get("base", {})
    if base.get("ref") != "main" or base.get("repo", {}).get("full_name") != REPOSITORY:
        msg = f"release PR #{number} does not target this repository's main"
        raise ChainError(msg)
    if not pr.get("merged") or pr.get("merge_commit_sha") != sha:
        msg = f"release PR #{number} does not match the unpublished merge on main"
        raise ChainError(msg)
    on_main(read, sha)
    verify_pr_content(read, pr, version)
    head = pr["head"]["sha"]
    tree = (read(f"commits/{head}") or {}).get("commit", {}).get("tree", {}).get("sha", "")
    if not SHA.fullmatch(str(tree)):
        msg = f"release PR #{number} has no verifiable source tree"
        raise ChainError(msg)
    verified_merge(read, int(number), pr, candidate_base(read, head), tree)
    msg = (
        f"v{version} already merged as {sha} (PR #{number}) and publication is pending; "
        "recover publication for that exact commit before cutting"
    )
    raise ChainError(msg)


def plan(  # noqa: PLR0913 - the plan's inputs, all keyword-only beyond the version
    read: Callable[[str], Any],
    requested: str,
    *,
    auto: bool,
    tip: str,
    pinned: bool = False,
    gate: bool = True,
) -> dict[str, str]:
    """What this run does: resume the release in flight, or cut `requested` (or the next patch) from `tip`.

    `pinned` (dry runs only) plans the cut of `requested` from the given main commit `tip` as the local driver did,
    whatever is in flight now: the previous release is the newest final one before `requested`. `gate=False` leaves
    main's CI gate to the caller (a dry run reports it instead of waiting for it).

    Returns:
        version, phase (cut, candidate, tail, done, idle), pr, bump, base, intro, previous and a reason line.

    Raises:
        ChainError: A cut is refused, or `requested` conflicts with the release in flight.
    """
    if requested:
        vkey(requested)
    if pinned:
        if not requested:
            msg = "a plan pinned to a cut SHA needs the version"
            raise ChainError(msg)
        on_main(read, tip)
        current = final_below(read, requested)
    else:
        current = newest_final(read)
        if found := in_flight(read, requested, current):
            return found
    if requested and vkey(requested) <= vkey(current):
        if has_marker(release_of(read, requested)):
            return step_plan(requested, "done", f"v{requested} is published and has its acceptance marker")
        msg = f"{requested} is not newer than the latest release {current}"
        raise ChainError(msg)
    tails = [v for v in in_tail(read) if vkey(v) < vkey(requested or "999999.0.0")]
    if len(tails) >= MAX_IN_FLIGHT:
        msg = f"{len(tails)} releases are in their tail ({', '.join(tails)}); at most {MAX_IN_FLIGHT} in flight"
        raise ChainError(msg)
    major, minor, patch = vkey(current)
    version = requested or f"{major}.{minor}.{patch + 1}"
    commits = compare_commits(read, f"v{current}", tip)
    if not pinned:
        check_unpublished_merge(read, commits, current)
    bump = bump_kind(current, version)
    subjects = [s for c in commits for s in commit_subjects(c.get("commit", {}).get("message", ""))]
    releasable = [s for s in subjects if not NOT_RELEASABLE.match(s)]
    if not commits or (auto and not releasable):
        return step_plan("", "idle", f"nothing releasable on main since v{current} ({len(commits)} commits)")
    proven = f"; proven by {main_gate(read, tip)}" if gate else ""
    behind = f"; pipelined behind v{tails[0]} in its tail" if tails else ""
    reason = f"cut v{version} from {tip[:9]}: {len(releasable)} releasable of {len(commits)} commits{proven}{behind}"
    return step_plan(version, "cut", reason, bump=bump, base=tip, intro=intro_from(releasable), previous=current)


def dry_plan(read: Callable[[str], Any], requested: str, *, tip: str, pinned: bool) -> tuple[dict[str, str], list[str]]:
    """The plan a real run would make, and main's CI gate as a report line: it neither waits nor fails on the gate.

    A refusal becomes the phase `refused`, so nothing after the plan runs. Returns the plan and the report lines.
    """
    try:
        result = plan(read, requested, auto=False, tip=tip, pinned=pinned, gate=False)
    except ChainError as exc:
        return step_plan(requested, "refused", f"a release would refuse: {exc}"), []
    lines = []
    if result["phase"] == "cut":
        try:
            lines.append(f"gate: main commit {tip[:9]} is proven by the {main_gate(read, tip)}")
        except Wait as exc:
            lines.append(f"gate: a release would wait: {exc}")
        except ChainError as exc:
            lines.append(f"gate: a release would refuse: {exc}")
    return result, lines


# ------------------------------------------------------------------------------------------------ the release commit


def git(*args: str, cwd: Path) -> str:
    """Read from the local checkout."""
    return run(["git", "-C", str(cwd), *args]).strip()


def verify_scope(cwd: Path, base: str, version: str) -> str:
    """The prepared HEAD is release/vX: one commit on `base` that only modifies the release files.

    Returns:
        The verified head SHA.

    Raises:
        ChainError: Anything else: another branch, base or parent, an added, deleted or re-moded file, a file outside
            the scope, or no version and lock change.
    """
    if git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd) != f"release/v{version}":
        msg = f"the checkout is not on release/v{version}"
        raise ChainError(msg)
    if git("status", "--porcelain", cwd=cwd):
        msg = "the prepared release checkout is dirty"
        raise ChainError(msg)
    head = git("rev-parse", "HEAD", cwd=cwd)
    if git("rev-list", "--parents", "-n", "1", head, cwd=cwd).split() != [head, base]:
        msg = f"the release commit is not one commit directly on {base[:9]}"
        raise ChainError(msg)
    records = run(["git", "-C", str(cwd), "diff", "--raw", "-z", "--no-abbrev", "--no-renames", base, head]).split("\0")
    if records[-1] or len(records) % 2 != 1:
        msg = "cannot parse the release diff"
        raise ChainError(msg)
    paths = set()
    for index in range(0, len(records) - 1, 2):
        if not re.fullmatch(r":(100644|100755) \1 [0-9a-f]{40} [0-9a-f]{40} M", records[index]):
            msg = f"the release diff adds, deletes or re-modes {records[index + 1]}"
            raise ChainError(msg)
        paths.add(records[index + 1])
    outside = sorted(paths - RELEASE_SCOPE)
    if outside or not {"pyproject.toml", "uv.lock"} <= paths:
        msg = f"the release diff is outside the release files ({', '.join(outside) or 'no version and lock change'})"
        raise ChainError(msg)
    if git("log", "-1", "--format=%s", head, cwd=cwd) != f"chore(release): v{version}":
        msg = f"the release commit is not titled chore(release): v{version}"
        raise ChainError(msg)
    return head


def section(changelog: str, version: str) -> str:
    """The `## [version]` section of CHANGELOG.md.

    Raises:
        ChainError: There is none.
    """
    match = re.search(rf"^## \[{re.escape(version)}\].*?$(.*?)(?=^## \[|\Z)", changelog, re.MULTILINE | re.DOTALL)
    if not match:
        msg = f"CHANGELOG.md has no section for {version}"
        raise ChainError(msg)
    return match.group(1)


def missing_from_notes(commits: list[tuple[str, str]], notes: str) -> list[str]:
    """Commits (sha, message) the notes do not name: a commit by its SHA, a combine by every member PR."""
    absent = []
    for sha, message in commits:
        if message.startswith("chore(release)"):
            continue
        members = set()
        for single, listed in MEMBERS.findall(message):
            members.update([single] if single else re.findall(r"#(\d+)", listed))
        if members:
            absent += [f"#{n} (in {sha[:9]})" for n in sorted(members, key=int) if not re.search(rf"#{n}\b", notes)]
        elif sha not in notes:
            absent.append(f"{sha[:9]} {message.splitlines()[0] if message else ''}".strip())
    return absent


def audit_notes(cwd: Path, previous: str, base: str, version: str) -> None:
    """Every commit from the previous tag to the cut base is in the new section.

    Raises:
        ChainError: Some are not.
    """
    log = git("log", "--format=%H%x1f%B%x1e", f"v{previous}..{base}", cwd=cwd)
    commits = [tuple(entry.strip().split("\x1f", 1)) for entry in log.split("\x1e") if entry.strip()]
    notes = section((cwd / "CHANGELOG.md").read_text(encoding="utf-8"), version)
    absent = missing_from_notes([(c[0], c[1] if len(c) > 1 else "") for c in commits], notes)
    if absent:
        msg = "the release notes do not name: " + "; ".join(absent)
        raise ChainError(msg)


# ------------------------------------------------------------------------------------------------ freeze


def freeze_targets(read: Callable[[str], Any]) -> tuple[list[tuple[int, str, bool]], int | None]:
    """(PR number, head SHA, passes) for every open PR, and the open release PR number, if any."""
    candidates = {p["number"] for p in open_release_prs(read)}
    frozen = bool(candidates)
    targets = [(p["number"], p["head"]["sha"], p["number"] in candidates or not frozen) for p in open_prs(read)]
    return targets, min(candidates) if candidates else None


def freeze_sync(read: Callable[[str], Any], post: Callable[[str, bool, str], None]) -> None:
    """Post the "Release freeze" check on every open PR head: failing while another PR is the release candidate."""
    targets, candidate = freeze_targets(read)
    text = (
        f"Release candidate #{candidate} is open: main is frozen until it merges."
        if candidate
        else "No release candidate is open."
    )
    for number, sha, passes in targets:
        post(sha, passes, text)
        say(f"{'pass' if passes else 'FAIL'} {FREEZE_CHECK} on #{number} ({sha[:9]})")


def freeze_of_pr(read: Callable[[str], Any], number: int) -> tuple[bool, str]:
    """The freeze verdict for one PR: it passes unless another PR is an open release candidate."""
    others = [p["number"] for p in open_release_prs(read) if p["number"] != number]
    if others:
        return False, f"Release candidate #{min(others)} is open: main is frozen until it merges."
    return True, "No other release candidate is open."


def post_check(sha: str, passes: bool, text: str) -> None:
    """Create one completed "Release freeze" check run with the workflow token."""
    run(
        [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{REPOSITORY}/check-runs",
            "-f",
            f"name={FREEZE_CHECK}",
            "-f",
            f"head_sha={sha}",
            "-f",
            "status=completed",
            "-f",
            f"conclusion={'success' if passes else 'failure'}",
            "-f",
            f"output[title]={'Not frozen' if passes else 'Frozen for a release'}",
            "-f",
            f"output[summary]={text}",
        ]
    )


# ------------------------------------------------------------------------------------------------ candidate and merge


def load_candidate_module() -> Any:  # noqa: ANN401 - the reviewed recorder module, loaded by path
    """scripts/release_candidate.py, the reviewed candidate reader."""
    spec = importlib.util.spec_from_file_location("release_candidate", Path(__file__).with_name("release_candidate.py"))
    assert spec  # noqa: S101 - the file is part of this checkout
    assert spec.loader  # noqa: S101
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def pr_of(read: Callable[[str], Any], number: int, version: str) -> dict[str, Any]:
    """The open canonical release PR `number` of `version`.

    Raises:
        ChainError: It is not that.
    """
    pr = read(f"pulls/{number}")
    if (
        not isinstance(pr, dict)
        or pr.get("head", {}).get("ref") != f"release/v{version}"
        or pr.get("title") != f"chore(release): v{version}"
        or pr.get("head", {}).get("repo", {}).get("full_name") != REPOSITORY
        or not SHA.fullmatch(str(pr.get("head", {}).get("sha", "")))
    ):
        msg = f"#{number} is not the release PR of {version}"
        raise ChainError(msg)
    return pr


def verify_pr_content(read: Callable[[str], Any], pr: dict[str, Any], version: str) -> None:
    """The release PR holds exactly the release commit, checked at every resume and right before the merge.

    One commit on its parent, titled chore(release): vX, that only modifies release files.

    Raises:
        ChainError: It holds anything else.
    """
    head = pr["head"]["sha"]
    base = candidate_base(read, head)
    data = read(f"compare/{base}...{head}")
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("commits"), list)
        or not isinstance(data.get("files"), list)
    ):
        msg = f"cannot compare the release PR #{pr.get('number')} with its base"
        raise ChainError(msg)
    commits = data["commits"]
    if data.get("total_commits") != 1 or len(commits) != 1 or commits[0].get("sha") != head:
        msg = f"release PR #{pr.get('number')} is not exactly one commit on {base[:9]}"
        raise ChainError(msg)
    subject = str(commits[0].get("commit", {}).get("message", "")).split("\n", 1)[0]
    if subject != f"chore(release): v{version}":
        msg = f"release PR #{pr.get('number')}: the commit is titled {subject!r}, not chore(release): v{version}"
        raise ChainError(msg)
    files = {str(f.get("filename")): f.get("status") for f in data["files"]}
    wrong = sorted(name for name, status in files.items() if name not in RELEASE_SCOPE or status != "modified")
    if wrong or not {"pyproject.toml", "uv.lock"} <= set(files):
        detail = ", ".join(f"{n} ({files[n]})" for n in wrong) or "no version and lock change"
        msg = f"release PR #{pr.get('number')} changes more than the release files: {detail}"
        raise ChainError(msg)


def candidate_base(read: Callable[[str], Any], head: str) -> str:
    """The one parent of the release commit: the main commit the candidate ran on.

    Raises:
        ChainError: It has not exactly one parent.
    """
    commit = read(f"commits/{head}")
    parents = [p.get("sha") for p in (commit or {}).get("parents", [])]
    if len(parents) != 1 or not SHA.fullmatch(str(parents[0])):
        msg = f"the release commit {head[:9]} has not exactly one parent"
        raise ChainError(msg)
    return str(parents[0])


def main_moved(read: Callable[[str], Any], head: str) -> str | None:
    """The refusal when main's tip is not the candidate base, else None."""
    base = candidate_base(read, head)
    tip = (read("commits/main") or {}).get("sha", "")
    if base == tip:
        return None
    return (
        f"main moved since the candidate base: the candidate ran on {base[:9]}, main is now {str(tip)[:9]}; its "
        "squash merge would carry unproven commits into the release. Nothing was merged"
    )


def verify_candidate(read: Callable[[str], Any], number: int, version: str) -> dict[str, Any]:
    """The release PR's CI: one run, first attempt, full inventory, the executed sources those of its head.

    Raises:
        Wait: CI has not finished.
        ChainError: The evidence refuses.
    """
    candidate = load_candidate_module()
    pr = pr_of(read, number, version)
    verify_pr_content(read, pr, version)
    head = pr["head"]["sha"]
    tree = read(f"commits/{head}")["commit"]["tree"]["sha"]

    def reader(path: str) -> Any:  # noqa: ANN401 - validated by the candidate module
        return read(path)

    rows = [r for r in ci_runs(read, head) if r.get("head_sha") == head]
    if not rows:
        msg = f"no CI run on the release head {head[:9]} yet"
        raise Wait(msg)
    if any(r.get("status") != "completed" for r in rows):
        msg = f"candidate CI on {head[:9]} is running"
        raise Wait(msg)
    try:
        run_id = candidate.discover(reader, head)
        pins = {
            path: hashlib.sha256(candidate.source_bytes(reader, path, head)).hexdigest()
            for path in (candidate.WORKFLOW, candidate.RECORDER)
        }
        evidence = candidate.verify(
            reader,
            download_artifact,
            candidate.ExpectedCandidate(run_id, number, head, tree),
            pins,
        )
    except candidate.CandidateError as exc:
        msg = f"the candidate is refused: {exc}"
        raise ChainError(msg) from exc
    return {"run_id": run_id, "head": head, "tree": tree} | (
        {"evidence": evidence} if isinstance(evidence, dict) else {}
    )


def release_pr_number(read: Callable[[str], Any], version: str) -> int | None:
    """The newest same-repository release PR of `version` in any state, else None."""
    rows = read(f"pulls?state=all&head={REPOSITORY.split('/', 1)[0]}:release/v{version}&per_page={PAGE}")
    numbers = [
        p["number"]
        for p in (rows if isinstance(rows, list) else [])
        if isinstance(p, dict)
        and p.get("head", {}).get("ref") == f"release/v{version}"
        and p.get("head", {}).get("repo", {}).get("full_name") == REPOSITORY
        and p.get("title") == f"chore(release): v{version}"
        and isinstance(p.get("number"), int)
    ]
    return max(numbers) if numbers else None


def dry_candidate(read: Callable[[str], Any], version: str, cut: str) -> tuple[bool, list[str]]:
    """The candidate verdict of `version`'s release PR, read once and never waited for; nothing is written.

    Returns False only when the candidate is refused (or its base is not the given cut SHA), with the report lines.
    """
    number = release_pr_number(read, version) if version else None
    if number is None:
        return True, [f"candidate: no release PR of v{version or '(none planned)'}; no candidate verdict"]
    state = (read(f"pulls/{number}") or {}).get("state")
    if state != "open":
        why = f"the release PR is {state}; the candidate is verified only while its PR is open"
        return True, [f"candidate #{number}: {why}: dispatch the dry run between the candidate CI and the merge"]
    try:
        evidence = verify_candidate(read, number, version)
    except Wait as exc:
        return True, [f"candidate #{number}: not finished: {exc}"]
    except ChainError as exc:
        return False, [f"candidate #{number}: REFUSED: {exc}"]
    lines = [f"candidate #{number}: OK, run {evidence['run_id']} on {evidence['head'][:9]}: full, first attempt"]
    base = candidate_base(read, evidence["head"])
    if cut and base != cut:
        lines.append(f"candidate #{number}: REFUSED: it ran on {base[:9]}, not on the cut SHA {cut[:9]}")
        return False, lines
    return True, lines


def required_checks(read: Callable[[str], Any]) -> list[str]:
    """The status check names the rules of main require (read from GitHub, never a copy).

    Raises:
        ChainError: The rules are unreadable or require no check: a merge would be unguarded.
    """
    rules = read("rules/branches/main")
    if not isinstance(rules, list):
        msg = "cannot read the rules of main"
        raise ChainError(msg)
    names = sorted(
        {
            str(check.get("context"))
            for rule in rules
            if isinstance(rule, dict) and rule.get("type") == "required_status_checks"
            for check in rule.get("parameters", {}).get("required_status_checks", [])
            if isinstance(check, dict) and check.get("context")
        }
    )
    if not names:
        msg = "the rules of main require no status check; refusing an unguarded merge"
        raise ChainError(msg)
    return names


def newest_check_runs(read: Callable[[str], Any], head: str) -> dict[str, dict[str, Any]]:
    """The newest check run of each name on `head`.

    Raises:
        ChainError: The listing is unreadable.
    """
    runs: dict[str, dict[str, Any]] = {}
    for page in range(1, 11):
        data = read(f"commits/{head}/check-runs?per_page={PAGE}&page={page}")
        rows = data.get("check_runs") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            msg = f"cannot read the check runs of {head[:9]}"
            raise ChainError(msg)
        for row in rows:
            name = str(row.get("name"))
            if name not in runs or int(row.get("id", 0)) > int(runs[name].get("id", 0)):
                runs[name] = row
        if len(rows) < PAGE:
            break
    return runs


def check_state(run: dict[str, Any] | None, status: dict[str, Any] | None) -> str:
    """pass, wait or the failing conclusion of one required check, from its check run, else its commit status."""
    if run:
        if run.get("status") != "completed":
            return "wait"
        return "pass" if run.get("conclusion") in PASSING else str(run.get("conclusion"))
    if status:
        state = status.get("state")
        return {"success": "pass", "pending": "wait"}.get(str(state), str(state))
    return "wait"


def checks_pass(read: Callable[[str], Any], head: str, required: list[str]) -> None:
    """Every required check is reported on `head` and passed (success, neutral or skipped, as GitHub counts them).

    The newest check run of a name counts, else the commit status of that context; the merge state is never read.

    Raises:
        Wait: A required check is missing or still running.
        ChainError: A required check failed.
    """
    runs = newest_check_runs(read, head)
    status = read(f"commits/{head}/status")
    rows = status.get("statuses", []) if isinstance(status, dict) else []
    contexts = {str(s.get("context")): s for s in rows if isinstance(s, dict)}
    states = {name: check_state(runs.get(name), contexts.get(name)) for name in required}
    if failed := [f"{name} ({state})" for name, state in states.items() if state not in {"pass", "wait"}]:
        msg = f"required checks failed on {head[:9]}: {', '.join(failed)}; nothing was merged"
        raise ChainError(msg)
    if waiting := [name for name, state in states.items() if state == "wait"]:
        msg = f"required checks not passed yet on {head[:9]}: {', '.join(waiting)}"
        raise Wait(msg)


def merge(read: Callable[[str], Any], number: int, version: str, token: str, proof: tuple[str, str, str]) -> str:
    """Squash-merge the verified release PR as the release identity, pinned to its head, if main has not moved.

    Returns:
        The release commit on main.

    Raises:
        Wait: A required check of the rules of main has not passed on the head yet.
        ChainError: Main moved, a required check failed, a review thread is open, or the merge did not land as the
            expected commit.
    """
    head, tree, run_id = proof
    if not (SHA.fullmatch(head) and SHA.fullmatch(tree) and run_id.isdecimal() and int(run_id) > 0):
        msg = "missing or invalid verified candidate head, tree or run"
        raise ChainError(msg)
    pr = read(f"pulls/{number}")
    if (pr or {}).get("head", {}).get("sha") != head:
        msg = "release PR head differs from the verified candidate"
        raise ChainError(msg)
    if (pr or {}).get("merged"):
        return verified_merge(read, number, pr, candidate_base(read, head), tree)
    pr = pr_of(read, number, version)
    verify_pr_content(read, pr, version)
    if refusal := main_moved(read, head):
        raise ChainError(refusal)
    checks_pass(read, head, required_checks(read))
    fresh = verify_candidate(read, number, version)
    if (fresh["head"], fresh["tree"], str(fresh["run_id"])) != proof:
        msg = "candidate head, tree or first-attempt run moved after verification"
        raise ChainError(msg)
    base = candidate_base(read, head)
    if refusal := main_moved(read, head):
        raise ChainError(refusal)
    run(
        [
            "gh",
            "pr",
            "merge",
            str(number),
            "-R",
            REPOSITORY,
            "--squash",
            "--match-head-commit",
            head,
            "--subject",
            f"chore(release): v{version} (#{number})",
            "--body",
            "",
        ],
        env=gh_env(token),
    )
    return verified_merge(read, number, read(f"pulls/{number}"), base, tree)


def verified_merge(read: Callable[[str], Any], number: int, merged: dict[str, Any] | None, base: str, tree: str) -> str:
    """A merge or retry may proceed only with the tested tree on its single validated parent.

    Raises:
        ChainError: The merge did not land as proven.
    """
    sha = str((merged or {}).get("merge_commit_sha", ""))
    if not (merged or {}).get("merged") or not SHA.fullmatch(sha) or candidate_base(read, sha) != base:
        msg = f"release PR #{number} did not merge as one commit on {base[:9]}"
        raise ChainError(msg)
    if (read(f"commits/{sha}") or {}).get("commit", {}).get("tree", {}).get("sha") != tree:
        msg = f"release PR #{number} merged with a different tree than the verified candidate"
        raise ChainError(msg)
    return sha


# ------------------------------------------------------------------------------------------------ the token probe


def probe(read: Callable[[str], Any], sh: Callable[..., str], repo: Path, token: str) -> tuple[bool, list[str]]:
    """Prove the owner's token on throwaway objects: push a branch, open a draft PR, freeze post and lift, rules.

    The branch `release-probe/<sha7>` holds one empty commit on main's tip; it is no release branch, so neither the
    freeze nor the release PR readers take it for a candidate. The PR and the branch are removed again, whatever
    happened. Nothing is merged, published or dispatched.

    Returns:
        Whether every probed step worked, and one line per step.
    """
    tip = str((read("commits/main") or {}).get("sha", ""))
    if not SHA.fullmatch(tip):
        return False, ["FAIL cannot read main's tip"]
    lines: list[str] = []
    branch = f"{PROBE_BRANCH}{tip[:7]}"
    made = {"token": token, "branch": branch, "remote": f"https://x-access-token:{token}@github.com/{REPOSITORY}.git"}
    remote = made["remote"]
    good = True
    try:
        login, uid = sh(["gh", "api", "user", "--jq", '"\\(.login) \\(.id)"'], env=gh_env(token)).split()
        lines.append(f"ok   the token acts as {login}")
        author = f"{uid}+{login}@users.noreply.github.com"
        env = gh_env(token) | {
            "GIT_AUTHOR_NAME": login,
            "GIT_AUTHOR_EMAIL": author,
            "GIT_COMMITTER_NAME": login,
            "GIT_COMMITTER_EMAIL": author,
        }
        subject = f"chore(probe): release token probe {tip[:7]}"
        sh(["git", "-C", str(repo), "checkout", "-q", "--detach", tip], env=env)
        sh(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", subject], env=env)
        head = sh(["git", "-C", str(repo), "rev-parse", "HEAD"], env=env).strip()
        sh(["git", "-C", str(repo), "push", "--quiet", remote, f"HEAD:refs/heads/{branch}"], env=env)
        made["pushed"] = "yes"
        lines.append(f"ok   pushed {branch} ({head[:9]})")
        body = "Opened and closed by the Release workflow's token probe (a dry run). Never merged."
        create = ["gh", "pr", "create", "-R", REPOSITORY, "--draft", "--base", "main", "--head", branch]
        url = sh([*create, "--title", subject, "--body", body], env=gh_env(token)).strip()
        number = int(url.rsplit("/", 1)[-1])
        made["pr"] = str(number)
        lines.append(f"ok   opened the draft PR #{number} as {login}")
        freeze_sync(read, post_check)
        if FREEZE_CHECK in newest_check_runs(read, head):
            lines.append(f"ok   {FREEZE_CHECK} posted on #{number}")
        else:
            good = False
            lines.append(f"FAIL {FREEZE_CHECK} did not reach #{number}")
        lines.append("ok   the merge waits for the required checks: " + ", ".join(required_checks(read)))
        lines.append("not probed: pypi.yml and compose.yml have no dry-run input; nothing was dispatched")
    except (ChainError, ValueError) as exc:
        good = False
        lines.append(f"FAIL {exc}")
    finally:
        good &= probe_cleanup(read, sh, made, lines)
    return good, lines


def probe_cleanup(read: Callable[[str], Any], sh: Callable[..., str], made: dict[str, str], lines: list[str]) -> bool:
    """Close the probe PR and remove its branch (`made`: what the probe created), then lift the freeze checks again.

    Returns:
        Whether that worked.
    """
    token, branch, remote = made["token"], made["branch"], made["remote"]
    number, pushed = made.get("pr", ""), bool(made.get("pushed"))
    try:
        if number:
            sh(["gh", "pr", "close", number, "-R", REPOSITORY, "--delete-branch"], env=gh_env(token))
        if pushed and read(f"git/ref/heads/{branch}") is not None:
            sh(["git", "push", "--quiet", remote, "--delete", branch], env=gh_env(token))
        if pushed and read(f"git/ref/heads/{branch}") is not None:
            lines.append(f"FAIL {branch} is still there")
            return False
        freeze_sync(read, post_check)
    except ChainError as exc:
        lines.append(f"FAIL cleanup: {exc}")
        return False
    if pushed:
        lines.append(f"ok   closed #{number or '-'} and removed {branch}; the freeze checks are lifted")
    return True


# ------------------------------------------------------------------------------------------------ the tail


def pypi_wheel_digest(version: str) -> str | None:
    """The sha256 PyPI serves for the Core wheel of `version`, else None."""
    url = f"https://pypi.org/pypi/heinzel/{version}/json"
    try:
        done = subprocess.run(  # noqa: S603
            ["curl", "-fsSL", "--max-time", "30", url],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None
    if done.returncode:
        return None
    name = f"heinzel-{version}-py3-none-any.whl"
    files = [f for f in json.loads(done.stdout).get("urls", []) if f.get("filename") == name and not f.get("yanked")]
    return files[0]["digests"]["sha256"] if len(files) == 1 else None


def validate_profile(content: bytes, version: str, wheel: str, app: dict[str, Any]) -> str:
    """The profile digest, after binding every artifact to the app release and the Core wheel.

    Raises:
        ChainError: The profile selects anything else.
    """
    digest = asset_digest(app, "standard.json")
    if not digest or hashlib.sha256(content).hexdigest() != digest:
        msg = "standard.json differs from its provider digest"
        raise ChainError(msg)
    profile = json.loads(content)
    artifacts = profile.get("artifacts") if isinstance(profile, dict) else None
    if not isinstance(artifacts, list) or not artifacts or profile.get("core_version") != version:
        msg = "standard.json selects another Core version or no artifacts"
        raise ChainError(msg)
    base = f"https://github.com/{PLUGINS}/releases/download/app-v{version}/"
    names = set()
    for artifact in artifacts:
        name, filename = artifact.get("name"), artifact.get("filename")
        if (
            not isinstance(name, str)
            or name in names
            or artifact.get("url") != f"{base}{filename}"
            or artifact.get("sha256") != asset_digest(app, str(filename))
        ):
            msg = f"standard.json artifact {name!r} is outside the app release"
            raise ChainError(msg)
        names.add(name)
        if name == "heinzel" and (artifact.get("version") != version or artifact.get("sha256") != wheel):
            msg = "standard.json selects another Core wheel"
            raise ChainError(msg)
    if "heinzel" not in names:
        msg = "standard.json has no Core wheel"
        raise ChainError(msg)
    return digest


def snapshot(
    read: Callable[[str], Any],
    read_app: Callable[[str], dict[str, Any] | None],
    version: str,
    fetch: Callable[[str], bytes],
) -> dict[str, str]:
    """The exact bytes acceptance must prove: the GitHub wheel (equal on PyPI) and the composed profile.

    Raises:
        Wait: The release, PyPI or the app is not there yet.
        ChainError: They disagree.
    """
    release = release_of(read, version)
    if not release:
        msg = f"v{version} is not published on GitHub yet"
        raise Wait(msg)
    wheel = asset_digest(release, f"heinzel-{version}-py3-none-any.whl")
    if not wheel or not asset_digest(release, "artifact-manifest.json"):
        msg = f"v{version} has no wheel or producer manifest with a provider digest yet"
        raise Wait(msg)
    pypi = pypi_wheel_digest(version)
    if pypi is None:
        msg = f"PyPI has no heinzel {version} wheel yet"
        raise Wait(msg)
    if pypi != wheel:
        msg = f"the PyPI wheel {pypi[:12]} differs from the GitHub wheel {wheel[:12]}"
        raise ChainError(msg)
    app = read_app(version)
    if not app or app.get("draft") is not False or app.get("prerelease") is not False:
        msg = f"app-v{version} is not composed and final yet"
        raise Wait(msg)
    profile = validate_profile(
        fetch(f"https://github.com/{PLUGINS}/releases/download/app-v{version}/standard.json"), version, wheel, app
    )
    commit = (read(f"commits/v{version}") or {}).get("sha", "")
    return {"version": version, "commit": str(commit), "wheel_sha256": wheel, "profile_sha256": profile}


def get_app(version: str) -> dict[str, Any] | None:
    """The plugins repository's app-vX.Y.Z release, else None."""
    app = get(f"releases/tags/app-v{version}", repo=PLUGINS)
    return app if isinstance(app, dict) and app.get("tag_name") == f"app-v{version}" else None


def download_artifact(artifact: int) -> bytes:
    """The zip bytes of one Actions artifact of Core.

    Raises:
        ChainError: It cannot be downloaded.
    """
    try:
        done = subprocess.run(  # noqa: S603
            ["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{artifact}/zip"],  # noqa: S607
            capture_output=True,
            check=False,
            timeout=COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"downloading the candidate source artifact {artifact} timed out"
        raise ChainError(msg) from exc
    if done.returncode:
        msg = f"cannot download the candidate source artifact {artifact}"
        raise ChainError(msg)
    return done.stdout


def fetch(url: str) -> bytes:
    """GET a public HTTPS asset (at most 1 MiB).

    Raises:
        ChainError: It is not one.
    """
    if not url.startswith("https://github.com/"):
        msg = f"refusing to fetch {url}"
        raise ChainError(msg)
    return run(["curl", "-fsSL", "--max-time", "60", "--max-filesize", str(1024 * 1024), url]).encode()


def sharded(result: dict[str, Any]) -> bool:
    """The receipt is the schema-2 reduction of the sharded acceptance: every shard has its own proof root."""
    sharding = result.get("sharding")
    if result.get("schema") != RECEIPT_SCHEMA or not isinstance(sharding, dict):
        return False
    shards, roots = sharding.get("shards"), sharding.get("proof_roots")
    return isinstance(shards, dict) and len(shards) > 1 and isinstance(roots, dict) and set(roots) == set(shards)


def receipt_ok(result: dict[str, Any], snap: dict[str, str], run_id: str, attempt: str, os_name: str) -> str | None:
    """None when the receipt proves the snapshot on `os_name` in run `run_id`, attempt `attempt`; else why not."""
    ci, published = result.get("ci", {}), result.get("published", {})
    checks = {
        "verdict": result.get("verdict") in ACCEPTED,
        "public path": result.get("public_path_proof") is True,
        "pinned": result.get("expectation_supplied") is True,
        "sharded": sharded(result),
        "repository": ci.get("repository") == REPOSITORY,
        "run": ci.get("run_id") == run_id and ci.get("run_attempt") == attempt,
        "runner": ci.get("runner_os") == os_name,
        "version": published.get("expect_version") == snap["version"]
        and published.get("expect_app_version") == snap["version"],
        "wheel": published.get("core_wheel_sha256") == snap["wheel_sha256"],
        "profile": published.get("profile_sha256") == snap["profile_sha256"],
    }
    failed = [name for name, ok in checks.items() if not ok]
    if not failed:
        return None
    why = f" {result['why']}" if result.get("why") else ""
    return f"{os_name}: {result.get('verdict')} ({', '.join(failed)}){why}"


def judge(folder: Path, snap: dict[str, str], run_id: str, attempt: str) -> tuple[bool, list[str]]:
    """Both receipts, one per OS, from this run's first attempt: accepted only if both prove the snapshot.

    Raises:
        ChainError: No verdict exists, so no marker may be written: a rerun attempt, or a receipt is missing (a job
            that never ran). Dispatching the Release workflow again runs acceptance afresh.
    """
    if attempt != "1":
        msg = f"attempt {attempt}: a verdict counts only from a run's first attempt; dispatch Release again"
        raise ChainError(msg)
    lines = []
    good = True
    results = {}
    for os_name, artifact in RECEIPTS.items():
        try:
            results[os_name] = json.loads((folder / artifact / "result.json").read_text())
        except (OSError, ValueError) as exc:
            msg = f"{os_name}: no readable receipt; no verdict, no marker: dispatch Release again"
            raise ChainError(msg) from exc
    for os_name, result in results.items():
        why = receipt_ok(result, snap, run_id, attempt, os_name)
        good &= why is None
        lines.append(why or f"{os_name}: {result.get('verdict')}")
    return good, lines


def older_open(read: Callable[[str], Any], version: str) -> None:
    """Before a release's marker and Latest, every older release is terminal: ACCEPTED, or not and so absorbed.

    Raises:
        Wait: An older release is still in its tail.
    """
    if older := [v for v in in_tail(read) if vkey(v) < vkey(version)]:
        msg = f"the older v{', v'.join(older)} is still in its tail; v{version} goes after it"
        raise Wait(msg)


def marker(version: str, run_id: str, good: bool) -> dict[str, Any]:
    """The acceptance.json the updater reads."""
    return {
        "schema": 1,
        "project": "heinzel",
        "version": version,
        "accepted": good,
        "acceptance_run": int(run_id),
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def publish_marker(read: Callable[[str], Any], version: str, data: dict[str, Any]) -> None:
    """Upload the marker once; a release that has one is terminal.

    Raises:
        ChainError: It has one already, or the upload is not visible.
    """
    if has_marker(release_of(read, version)):
        msg = f"v{version} has an acceptance marker already; it is terminal"
        raise ChainError(msg)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / MARKER
        path.write_text(json.dumps(data) + "\n")
        run(["gh", "release", "upload", f"v{version}", str(path), "-R", REPOSITORY])
    if not has_marker(release_of(read, version)):
        msg = f"the acceptance marker on v{version} is not visible"
        raise ChainError(msg)


def promote(read: Callable[[str], Any], version: str, snap: dict[str, str], *, dry: bool = False) -> None:
    """Mark the accepted release Latest, never behind a newer one, only with the accepted wheel.

    Raises:
        ChainError: It is not final, has no marker, the wheel changed, a newer release is Latest, or GitHub did not
            take it.
    """
    release = release_of(read, version)
    if not release or release.get("prerelease") is not False:
        msg = f"v{version} is no published final release"
        raise ChainError(msg)
    if not has_marker(release):
        msg = f"v{version} has no acceptance marker; nothing goes Latest before it"
        raise ChainError(msg)
    if asset_digest(release, f"heinzel-{version}-py3-none-any.whl") != snap["wheel_sha256"]:
        msg = f"the v{version} wheel differs from the accepted one"
        raise ChainError(msg)
    latest = (read("releases/latest") or {}).get("tag_name", "")
    if latest == f"v{version}":
        say(f"ok   v{version} is Latest already")
        return
    if (
        isinstance(latest, str)
        and latest.startswith("v")
        and VERSION.fullmatch(latest[1:])
        and vkey(latest[1:]) > vkey(version)
    ):
        msg = f"{latest} is Latest and newer; v{version} stays as it is"
        raise ChainError(msg)
    if dry:
        say(f"dry  would mark v{version} Latest (now {latest or 'none'})")
        return
    run(["gh", "release", "edit", f"v{version}", "--latest", "-R", REPOSITORY])
    if (read("releases/latest") or {}).get("tag_name") != f"v{version}":
        msg = f"v{version} is still not Latest"
        raise ChainError(msg)
    say(f"ok   v{version} is Latest: the updater offers it now")


# ------------------------------------------------------------------------------------------------ CLI


def poll(label: str, step: Callable[[], Any], timeout: float) -> Any:  # noqa: ANN401 - the step's own result
    """Run `step` until it stops raising Wait, at most `timeout` seconds.

    Raises:
        ChainError: The deadline passed.
    """
    deadline = time.monotonic() + timeout
    last = ""
    while True:
        try:
            return step()
        except Wait as exc:
            if str(exc) != last:
                say(f"wait {label}: {exc}")
                last = str(exc)
            if time.monotonic() > deadline:
                msg = f"{label}: timed out after {timeout / 60:.0f} min ({exc})"
                raise ChainError(msg) from exc
            time.sleep(POLL)


def output(values: dict[str, str]) -> None:
    """Write step outputs (and echo them)."""
    target = os.environ.get(RUN_OUTPUT)
    for key, value in values.items():
        say(f"{key}={value}")
    if target:
        with Path(target).open("a", encoding="utf-8") as handle:
            for key, value in values.items():
                handle.write(f"{key}={value.replace(chr(10), ' ')}\n")


def parser() -> argparse.ArgumentParser:
    """The command line: one subcommand per chain step."""
    top = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = top.add_subparsers(dest="command", required=True)
    options: dict[str, tuple[tuple[str, dict[str, Any]], ...]] = {
        "plan": (
            ("--version", {"default": ""}),
            ("--auto", {"action": "store_true"}),
            ("--tip", {"required": True}),
            ("--wait", {"type": float, "default": 3600}),
            ("--pinned", {"action": "store_true"}),
            ("--dry-run", {"action": "store_true"}),
        ),
        "scope": (
            ("--repo", {"type": Path, "default": Path()}),
            ("--base", {"required": True}),
            ("--version", {"required": True}),
            ("--previous", {"required": True}),
        ),
        "freeze-sync": (),
        "need-token": (),
        "freeze-pr": (("--pr", {"type": int, "required": True}),),
        "candidate": (
            ("--pr", {"type": int}),
            ("--version", {"required": True}),
            ("--wait", {"type": float, "default": 4 * 3600}),
            ("--dry-run", {"action": "store_true"}),
            ("--cut-sha", {"default": ""}),
        ),
        "merge": (
            ("--pr", {"type": int, "required": True}),
            ("--version", {"required": True}),
            ("--candidate-head", {"required": True}),
            ("--candidate-tree", {"required": True}),
            ("--candidate-run", {"required": True}),
            ("--wait", {"type": float, "default": 1800}),
        ),
        "probe": (("--repo", {"type": Path, "default": Path()}),),
        "snapshot": (("--version", {"required": True}), ("--wait", {"type": float, "default": 3600})),
        "verdict": (
            ("--version", {"required": True}),
            ("--receipts", {"type": Path, "required": True}),
            ("--run-id", {"required": True}),
            ("--attempt", {"required": True}),
            ("--wheel-sha256", {"required": True}),
            ("--profile-sha256", {"required": True}),
            ("--dry-run", {"action": "store_true"}),
            ("--wait", {"type": float, "default": 4 * 3600}),
        ),
    }
    for name, flags in options.items():
        sub = commands.add_parser(name)
        for flag, kwargs in flags:
            sub.add_argument(flag, **kwargs)
    return top


def cmd_plan(args: argparse.Namespace) -> int:
    """Decide what this run does; a dry run reports the gate instead of waiting for it."""
    if args.dry_run:
        result, lines = dry_plan(get, args.version, tip=args.tip, pinned=args.pinned)
    elif args.pinned:
        say("error: a plan pinned to a cut SHA is a dry run only")
        return 1
    else:
        result = poll("main tip CI", lambda: plan(get, args.version, auto=args.auto, tip=args.tip), args.wait)
        lines = []
    say(("dry run: " if args.dry_run else "") + result["reason"])
    for line in lines:
        say("  " + line)
    output({k: v for k, v in result.items() if k != "reason"} | {"previous": result["previous"] or newest_final(get)})
    return 0


def cmd_scope(args: argparse.Namespace) -> int:
    """Check the prepared release commit and its notes."""
    head = verify_scope(args.repo.resolve(), args.base, args.version)
    audit_notes(args.repo.resolve(), args.previous, args.base, args.version)
    say(f"ok   release commit {head[:9]}: only release files, notes name every commit since v{args.previous}")
    output({"head": head})
    return 0


def cmd_need_token(_: argparse.Namespace) -> int:
    """Refuse a real release early when the owner's token is not configured (HAS_RELEASE_TOKEN from the workflow)."""
    if os.environ.get("HAS_RELEASE_TOKEN") == "true":
        return 0
    say(f"error: {NO_RELEASE_IDENTITY}")
    return 1


def cmd_freeze_sync(_: argparse.Namespace) -> int:
    """Post the freeze check on every open PR."""
    freeze_sync(get, post_check)
    return 0


def cmd_freeze_pr(args: argparse.Namespace) -> int:
    """The freeze verdict of one PR, as the exit code."""
    passes, text = freeze_of_pr(get, args.pr)
    say(text)
    return 0 if passes else 1


def cmd_candidate(args: argparse.Namespace) -> int:
    """Wait for and verify the candidate CI; a dry run reads the version's release PR once and writes nothing."""
    if args.dry_run:
        good, lines = dry_candidate(get, args.version, args.cut_sha)
        for line in lines:
            say(line)
        return 0 if good else 1
    if args.pr is None:
        say("error: candidate needs --pr")
        return 1
    evidence = poll("candidate CI", lambda: verify_candidate(get, args.pr, args.version), args.wait)
    say(f"ok   candidate run {evidence['run_id']} on {evidence['head'][:9]}: full, first attempt")
    output({"run_id": str(evidence["run_id"]), "head": evidence["head"], "tree": evidence["tree"]})
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    """Merge the verified release PR as the release identity."""
    token = os.environ.get("RELEASE_TOKEN", "")
    if not token:
        say(f"error: {NO_RELEASE_IDENTITY}")
        return 1
    proof = (args.candidate_head, args.candidate_tree, args.candidate_run)
    sha = poll("required checks", lambda: merge(get, args.pr, args.version, token, proof), args.wait)
    say(f"ok   merged release commit {sha[:9]}")
    output({"sha": sha})
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """The token probe of a dry run (workflow job "Token probe")."""
    token = os.environ.get("RELEASE_TOKEN", "")
    if not token:
        say(f"error: {NO_RELEASE_IDENTITY}")
        return 1
    good, lines = probe(get, run, args.repo.resolve(), token)
    for line in lines:
        say(line)
    return 0 if good else 1


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Wait for and bind the published bytes."""
    output(poll("release bytes", lambda: snapshot(get, get_app, args.version, fetch), args.wait))
    return 0


def cmd_verdict(args: argparse.Namespace) -> int:
    """Judge the receipts, then the marker, then Latest."""
    snap = {"version": args.version, "wheel_sha256": args.wheel_sha256, "profile_sha256": args.profile_sha256}
    good, lines = judge(args.receipts, snap, args.run_id, args.attempt)
    for line in lines:
        say("  " + line)
    say(f"{'ACCEPTED' if good else 'NOT ACCEPTED'} {args.version} (run {args.run_id})")
    if args.dry_run:
        try:
            older_open(get, args.version)
        except Wait as exc:
            say(f"dry  would wait: {exc}")
        promote(get, args.version, snap, dry=True)
        return 0 if good else 1
    poll("older release", lambda: older_open(get, args.version), args.wait)
    publish_marker(get, args.version, marker(args.version, args.run_id, good))
    say(f"ok   acceptance marker on v{args.version}: {'accepted' if good else 'not accepted'}")
    if not good:
        return 1
    promote(get, args.version, snap)
    return 0


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "plan": cmd_plan,
    "scope": cmd_scope,
    "freeze-sync": cmd_freeze_sync,
    "need-token": cmd_need_token,
    "freeze-pr": cmd_freeze_pr,
    "candidate": cmd_candidate,
    "merge": cmd_merge,
    "snapshot": cmd_snapshot,
    "probe": cmd_probe,
    "verdict": cmd_verdict,
}


def main(argv: list[str] | None = None) -> int:
    """Run one chain step; a refusal prints its reason and exits 1."""
    args = parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except ChainError as exc:
        say(f"error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
