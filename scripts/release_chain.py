# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///
"""The Core release chain in GitHub Actions (workflow "Release"), one step per command.

Every step reads GitHub state first, so a new run of the workflow continues where the last one stopped, and the
release completes with no machine of ours awake.

Order and rules (they are the rules the local driver kept):
- plan: the version, and what is done already. A cut needs main's tip green on the FIRST attempt of its push run
  (no known-flake exception: CI cannot read beads), no red macOS lane on that tip, the previous release terminal
  (its `acceptance.json` exists), releasable commits since the last tag.
- scope: the release commit is one commit on that tip and changes only the release files.
- notes: the new CHANGELOG.md section names every commit since the last tag (a combine by all its members).
- freeze: while a release PR is open, the check "Release freeze" fails on every other open PR.
- candidate: the release PR's one CI run, first attempt, full job inventory (scripts/release_candidate.py).
- merge: only when main has not moved since the candidate base; squash, pinned to the verified head.
- snapshot: after the GitHub release, PyPI and the composed app exist: the wheel and profile digests, with the
  GitHub and PyPI wheel equal.
- verdict: both acceptance receipts of this run's first attempt prove those digests; then the marker; then Latest.

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
INTRO_ITEMS = 4
INTRO_LIMIT = 600
PAGE = 100
POLL = 20
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
    done = subprocess.run(argv, capture_output=True, text=True, check=False, env=env)  # noqa: S603
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
    done = subprocess.run(  # noqa: S603
        ["gh", "api", f"repos/{repo}/{path}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
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


def main_gate(read: Callable[[str], Any], tip: str) -> None:
    """Main's tip may be cut: its push run is green on attempt 1 and no macOS lane run of it is red.

    Raises:
        Wait: The push run is not finished.
        ChainError: It is red, green only on a rerun, missing or ambiguous; or a macOS lane run is red.
    """
    rows = ci_runs(read, tip)
    pushed = [r for r in rows if r.get("event") == "push"]
    if not pushed:
        msg = f"main tip {tip[:9]} has no CI push run yet"
        raise Wait(msg)
    if len(pushed) != 1:
        msg = f"main tip {tip[:9]} has {len(pushed)} CI push runs; refusing to pick one"
        raise ChainError(msg)
    push = pushed[0]
    where = f"main tip {tip[:9]} (run {push.get('id')})"
    if push.get("status") != "completed":
        msg = f"{where}: CI is {push.get('status')}"
        raise Wait(msg)
    if push.get("conclusion") != "success":
        msg = f"{where}: CI ended {push.get('conclusion')}; fix main first"
        raise ChainError(msg)
    if push.get("run_attempt") != 1:
        msg = f"{where}: green only on attempt {push.get('run_attempt')}; a release needs a first-attempt green run"
        raise ChainError(msg)
    for lane in rows:
        if (
            lane.get("event") == "workflow_dispatch"
            and lane.get("status") == "completed"
            and lane.get("conclusion") in RED
        ):
            msg = f"main tip {tip[:9]}: the macOS lane run {lane.get('id')} is {lane.get('conclusion')}; fix main first"
            raise ChainError(msg)


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
    return {"version": version, "phase": phase, "pr": "", "bump": "", "base": "", "intro": "", "reason": reason} | extra


def in_flight(read: Callable[[str], Any], requested: str, current: str) -> dict[str, str] | None:
    """The release already under way (an open release PR, or a published release without its marker), else None.

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
        for release in releases(read):
            tag = release.get("tag_name", "")
            version = tag[1:] if isinstance(tag, str) and tag.startswith("v") else ""
            if (
                VERSION.fullmatch(version)
                and release.get("draft") is False
                and not has_marker(release)
                and vkey(version) >= vkey(current)
            ):
                found = step_plan(version, "tail", f"resume: v{version} is published, its acceptance is open")
                break
    if found and requested and requested != found["version"]:
        msg = f"v{found['version']} is in flight ({found['reason']}); finish it before {requested}"
        raise ChainError(msg)
    return found


def plan(read: Callable[[str], Any], requested: str, *, auto: bool, tip: str) -> dict[str, str]:
    """What this run does: resume the release in flight, or cut `requested` (or the next patch) from `tip`.

    Returns:
        version, phase (cut, candidate, tail, done, idle), pr, bump, base, intro and a reason line.

    Raises:
        ChainError: A cut is refused, or `requested` conflicts with the release in flight.
    """
    if requested:
        vkey(requested)
    current = newest_final(read)
    if found := in_flight(read, requested, current):
        return found
    if requested and vkey(requested) <= vkey(current):
        if has_marker(release_of(read, requested)):
            return step_plan(requested, "done", f"v{requested} is published and has its acceptance marker")
        msg = f"{requested} is not newer than the latest release {current}"
        raise ChainError(msg)
    if not has_marker(release_of(read, current)):
        msg = f"the previous release v{current} has no acceptance marker yet; it is not terminal"
        raise ChainError(msg)
    major, minor, patch = vkey(current)
    version = requested or f"{major}.{minor}.{patch + 1}"
    bump = bump_kind(current, version)
    commits = compare_commits(read, f"v{current}", tip)
    subjects = [s for c in commits for s in commit_subjects(c.get("commit", {}).get("message", ""))]
    releasable = [s for s in subjects if not NOT_RELEASABLE.match(s)]
    if not commits or (auto and not releasable):
        return step_plan("", "idle", f"nothing releasable on main since v{current} ({len(commits)} commits)")
    main_gate(read, tip)
    reason = f"cut v{version} from {tip[:9]}: {len(releasable)} releasable of {len(commits)} commits"
    return step_plan(version, "cut", reason, bump=bump, base=tip, intro=intro_from(releasable))


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
    return {"run_id": run_id, "head": head} | ({"evidence": evidence} if isinstance(evidence, dict) else {})


def merge(read: Callable[[str], Any], number: int, version: str, token: str) -> str:
    """Squash-merge the verified release PR as the release identity, pinned to its head, if main has not moved.

    Returns:
        The release commit on main.

    Raises:
        ChainError: Main moved, a review thread is open, or the merge did not land as the expected commit.
    """
    pr = read(f"pulls/{number}")
    if isinstance(pr, dict) and pr.get("merged"):
        return str(pr["merge_commit_sha"])
    pr = pr_of(read, number, version)
    verify_pr_content(read, pr, version)
    head = pr["head"]["sha"]
    if refusal := main_moved(read, head):
        raise ChainError(refusal)
    base = candidate_base(read, head)
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
    merged = read(f"pulls/{number}")
    sha = str((merged or {}).get("merge_commit_sha", ""))
    if not (merged or {}).get("merged") or not SHA.fullmatch(sha) or candidate_base(read, sha) != base:
        msg = f"release PR #{number} did not merge as one commit on {base[:9]}"
        raise ChainError(msg)
    return sha


# ------------------------------------------------------------------------------------------------ the tail


def pypi_wheel_digest(version: str) -> str | None:
    """The sha256 PyPI serves for the Core wheel of `version`, else None."""
    url = f"https://pypi.org/pypi/heinzel/{version}/json"
    done = subprocess.run(["curl", "-fsSL", "--max-time", "30", url], capture_output=True, text=True, check=False)  # noqa: S603, S607
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
    done = subprocess.run(  # noqa: S603
        ["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{artifact}/zip"],  # noqa: S607
        capture_output=True,
        check=False,
    )
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


def receipt_ok(result: dict[str, Any], snap: dict[str, str], run_id: str, attempt: str, os_name: str) -> str | None:
    """None when the receipt proves the snapshot on `os_name` in run `run_id`, attempt `attempt`; else why not."""
    ci, published = result.get("ci", {}), result.get("published", {})
    checks = {
        "verdict": result.get("verdict") in ACCEPTED,
        "public path": result.get("public_path_proof") is True,
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
        ),
        "scope": (
            ("--base", {"required": True}),
            ("--version", {"required": True}),
            ("--previous", {"required": True}),
        ),
        "freeze-sync": (),
        "need-token": (),
        "freeze-pr": (("--pr", {"type": int, "required": True}),),
        "candidate": (
            ("--pr", {"type": int, "required": True}),
            ("--version", {"required": True}),
            ("--wait", {"type": float, "default": 4 * 3600}),
        ),
        "merge": (("--pr", {"type": int, "required": True}), ("--version", {"required": True})),
        "snapshot": (("--version", {"required": True}), ("--wait", {"type": float, "default": 3600})),
        "verdict": (
            ("--version", {"required": True}),
            ("--receipts", {"type": Path, "required": True}),
            ("--run-id", {"required": True}),
            ("--attempt", {"required": True}),
            ("--wheel-sha256", {"required": True}),
            ("--profile-sha256", {"required": True}),
            ("--dry-run", {"action": "store_true"}),
        ),
    }
    for name, flags in options.items():
        sub = commands.add_parser(name)
        for flag, kwargs in flags:
            sub.add_argument(flag, **kwargs)
    return top


def cmd_plan(args: argparse.Namespace) -> int:
    """Decide what this run does."""
    result = poll("main tip CI", lambda: plan(get, args.version, auto=args.auto, tip=args.tip), args.wait)
    say(result["reason"])
    output({k: v for k, v in result.items() if k != "reason"} | {"previous": newest_final(get)})
    return 0


def cmd_scope(args: argparse.Namespace) -> int:
    """Check the prepared release commit and its notes."""
    head = verify_scope(Path.cwd(), args.base, args.version)
    audit_notes(Path.cwd(), args.previous, args.base, args.version)
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
    """Wait for and verify the candidate CI."""
    evidence = poll("candidate CI", lambda: verify_candidate(get, args.pr, args.version), args.wait)
    say(f"ok   candidate run {evidence['run_id']} on {evidence['head'][:9]}: full, first attempt")
    output({"run_id": str(evidence["run_id"]), "head": evidence["head"]})
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    """Merge the verified release PR as the release identity."""
    token = os.environ.get("RELEASE_TOKEN", "")
    if not token:
        say(f"error: {NO_RELEASE_IDENTITY}")
        return 1
    sha = merge(get, args.pr, args.version, token)
    say(f"ok   merged release commit {sha[:9]}")
    output({"sha": sha})
    return 0


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
        promote(get, args.version, snap, dry=True)
        return 0 if good else 1
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
