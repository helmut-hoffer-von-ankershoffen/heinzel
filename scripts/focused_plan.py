# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Resolve the administrator-reviewed workload for the exact Git tree tested by CI."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

# The isolated workflow launcher supplies only this exact checkout's modules.
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_workload import CandidatePlan, FocusedPlan, WorkloadSource, plan_workload
from scripts.focused_job import MAX_IDS, TOKEN
from scripts.release_candidate import (
    DIGEST,
    REPOSITORY,
    CandidateError,
    command,
    dispatch_pull,
    release_version,
    require,
    unique_object,
    valid_sha,
)

if TYPE_CHECKING:
    from collections.abc import Callable

PATH = re.compile(r"[A-Za-z0-9_.@+-]+(?:/[A-Za-z0-9_.@+-]+)*")
LANES = {
    "python": ("ubuntu-24.04", "3.14.8", "pytest"),
    "python312": ("ubuntu-24.04", "3.12", "pytest"),
    "python315": ("ubuntu-24.04", "3.15", "pytest"),
    "macos": ("macos-latest", "3.14", "pytest"),
    "macos-notifier": ("macos-latest", "3.14", "pytest"),
    "managed": ("ubuntu-24.04", "3.14", "managed"),
    "web": ("ubuntu-24.04", "3.14", "vitest"),
    "browser": ("ubuntu-24.04", "3.14", "e2e"),
    "macos-browser": ("macos-latest", "3.14", "e2e"),
}
ARTIFACT_PREFIX = "focused-pr-evidence"
PLAN_VARIABLE = "FOCUSED_CI_PLANS"
PREOPEN_PR_REF = "refs/pull/*/merge"
PROTECTED_FILES = (
    ".github/workflows/ci.yml",
    ".github/actions/setup-uv/action.yml",
    ".github/actions/setup-uv/uv-manifest.json",
    "scripts/bootstrap-pyyaml.sh",
    "scripts/ci_workload.py",
    "scripts/focused_plan.py",
    "scripts/focused_job.py",
    "scripts/focused_coverage.py",
    "scripts/focused_coverage.py.lock",
    "scripts/pyyaml-build-requirements.txt",
    "scripts/release_candidate.py",
    "uv.lock",
)
MAX_PLAN = 48 * 1024
MAX_DIFF = 1024 * 1024
MAX_COVERED_FILES = 100
RAW_FIELDS = 5
LS_TREE_FIELDS = 2


@dataclass(frozen=True)
class Scope:
    """Complete reviewed scope for the exact tested tree."""

    workload: FocusedPlan
    commit: str
    tree: str
    changed: dict[str, str]
    production: dict[str, str]
    config_blob: str
    execution: dict[str, str]
    selection: dict[str, tuple[str, ...]]
    case_digests: dict[str, str]
    manifest_digest: str

    @property
    def mode(self) -> str:
        """Describe the applicability of the existing Python coverage policy."""
        return "pr-scoped" if self.production else "python-coverage-not-applicable"

    def matrix(self) -> list[dict[str, str]]:
        """Return only fixed runner and command classes, without manifest inputs."""
        return [
            {"lane": name, "os": LANES[name][0], "python": LANES[name][1], "kind": LANES[name][2]}
            for name in self.selection
        ]


def git(*args: str) -> bytes:
    """Read Git objects from the caller's fresh checkout."""
    return command(["git", *args])


def object_id(revision: str) -> str:
    """Resolve the identity of a Git object."""
    value = git("rev-parse", "--verify", revision).decode().strip()
    require(valid_sha(value), "invalid Git object")
    return value


def git_blob(commit: str, path: str) -> str:
    """Bind a regular source blob, rejecting symlinks and submodule entries."""
    entry = git("ls-tree", "-z", commit, "--", path).split(b"\0")
    require(len(entry) == LS_TREE_FIELDS and entry[-1] == b"", f"missing or ambiguous tracked file: {path}")
    metadata, name = entry[0].split(b"\t")
    mode, kind, blob = metadata.decode().split()
    require(
        name.decode() == path and mode in {"100644", "100755"} and kind == "blob" and valid_sha(blob),
        f"unsupported tracked source file: {path}",
    )
    return blob


def _production(path: str) -> bool:
    return path.startswith("src/heinzel/") and path.endswith(".py")


def web_production(path: str) -> bool:
    """Recognize web production scope without adding a new numeric coverage policy."""
    return path.startswith("web/src/") and not re.search(r"\.(?:test|spec)\.[cm]?[jt]sx?$", path)


def _path(path: object) -> bool:
    return isinstance(path, str) and PATH.fullmatch(path) is not None and not {".", ".."}.intersection(path.split("/"))


def raw_diff(base: str, commit: str) -> bytes:
    """Get the exact reviewable Git diff with rename halves represented independently."""
    require(valid_sha(base) and valid_sha(commit), "missing exact Git identities")
    return git("diff-tree", "--no-commit-id", "-r", "--raw", "--no-abbrev", "--no-renames", "-z", base, commit)


def changed_paths(raw: bytes) -> dict[str, str]:
    """Extract a complete path/status inventory. Exceptional Git changes require explicit review."""
    require(len(raw) <= MAX_DIFF and (not raw or raw.endswith(b"\0")), "unsupported focused diff")
    parts = raw.split(b"\0")[:-1]
    require(not len(parts) % 2, "malformed focused diff")
    changed = {}
    for record, name in zip(parts[::2], parts[1::2], strict=True):
        fields = record.decode().split()
        path = name.decode()
        require(
            len(fields) == RAW_FIELDS and fields[-1] in {"A", "M", "D", "T"} and _path(path) and path not in changed,
            "unrecognized diff record requires explicit reviewed handling",
        )
        changed[path] = fields[-1]
    return changed


def _selections(plan: dict[str, Any], commit: str) -> dict[str, tuple[str, ...]]:
    rows = plan["selection"]
    require(isinstance(rows, dict) and rows and set(rows) <= set(LANES), "missing reviewed focused lane")
    selected = {}
    for lane, tests in rows.items():
        kind = LANES[lane][2]
        prefix = {"pytest": "tests/", "vitest": "web/", "e2e": "tests/e2e/", "managed": "desktop/managed/test/"}[kind]
        require(isinstance(tests, list) and 0 < len(tests) <= MAX_IDS, f"empty or broad focused lane {lane}")
        require(len(set(tests)) == len(tests), f"duplicate selection in {lane}")
        for test in tests:
            path = test.split("::", 1)[0]
            require(
                TOKEN.fullmatch(test) and _path(path) and path.startswith(prefix), f"invalid focused test in {lane}"
            )
            require(
                (kind == "vitest" and path == test and re.search(r"\.(?:test|spec)\.[cm]?[jt]sx?$", path))
                or (kind == "managed" and path == test and path.endswith(".test.ts"))
                or (kind in {"pytest", "e2e"} and path.endswith("_test.py")),
                "focused selections require owning test files or exact test node IDs",
            )
            require(kind != "pytest" or not path.startswith("tests/e2e/"), "browser tests require their owning lane")
            git_blob(commit, path)
        selected[lane] = tuple(tests)
    return selected


def resolve(
    source: WorkloadSource,
    commit: str,
    manifests: str,
    dispatch_pr: dict[str, Any] | None = None,
) -> CandidatePlan | Scope:
    """Resolve only an exact released candidate or exact reviewed, bounded workload.

    Raises:
        CandidateError: No exact focused plan or supported candidate was found.
    """
    # Full candidate recognition is deliberately independent of focused plans.
    if release_version(source.event, source.repository, source.ref.removeprefix("refs/heads/"), source.payload):
        workload = plan_workload(source, dispatch_pr=dispatch_pr)
        if isinstance(workload, CandidatePlan):
            return workload
        message = "canonical candidate source did not resolve to the full inventory"
        raise CandidateError(message)
    require(valid_sha(commit), "missing exact tested checkout")
    require(len(manifests.encode()) <= MAX_PLAN, "focused plan registry exceeds its supported size")
    registry = json.loads(manifests or "{}", object_pairs_hook=unique_object)
    require(isinstance(registry, dict), "invalid reviewed focused plan registry")
    plan = registry.get(f"{source.event}:{source.head}")
    preplanned_main = plan is None and source.event == "push" and source.ref == "refs/heads/main"
    if preplanned_main:
        tree = object_id(f"{commit}^{{tree}}")
        plan = registry.get(f"push-tree:{tree}")
    if not isinstance(plan, dict):
        message = "No focused plan for this exact source. Submit an independently reviewed scope manifest."
        raise CandidateError(message)
    require(
        plan["ref"] == source.ref or (source.event == "pull_request" and plan["ref"] == PREOPEN_PR_REF),
        "reviewed ref does not apply to the exact workflow event",
    )
    selection = _selections(plan, commit)
    workload = plan_workload(
        source,
        focused=FocusedPlan(
            plan["event"],
            source.ref,
            plan["base"],
            plan.get("head", source.head) if preplanned_main else plan["head"],
            plan["diff_sha256"],
            tuple(f"Focused tests ({lane})" for lane in selection),
        ),
    )
    if isinstance(workload, FocusedPlan):
        return _scope(source, workload, commit, plan, selection)
    message = "ordinary source did not resolve to a focused workload"
    raise CandidateError(message)


def _scope(
    source: WorkloadSource,
    workload: FocusedPlan,
    commit: str,
    plan: dict[str, Any],
    selection: dict[str, tuple[str, ...]],
) -> Scope:
    require(valid_sha(commit), "invalid tested checkout")
    if source.event == "pull_request":
        parents = git("rev-list", "--parents", "-n", "1", commit).decode().split()
        require(parents == [commit, source.base, source.head], "GitHub test merge does not bind ordered base and head")
    else:
        require(commit == source.head, "checkout differs from exact source head")
    tree = object_id(f"{commit}^{{tree}}")
    changes = changed_paths(source.raw_diff)
    production = plan["production"]
    changed_python = {path for path, status in changes.items() if _production(path) and status != "D"}
    require(
        type(plan["schema"]) is int
        and plan["schema"] == 1
        and plan.get("commit", commit) == commit
        and plan["tree"] == tree
        and plan["changed"] == changes
        and isinstance(production, dict)
        and len(production) <= MAX_COVERED_FILES
        and changed_python <= set(production)
        and all(_path(path) and _production(path) for path in production),
        "reviewed manifest does not cover the complete tested change and production scope",
    )
    require(plan["mode"] == ("pr-scoped" if production else "python-coverage-not-applicable"), "wrong coverage mode")
    require(not production or "python" in selection, "Python production requires a Linux coverage selection")
    if any(web_production(path) for path in changes):
        require(
            "web" in selection and bool({"browser", "macos-browser"} & selection.keys()),
            "web production requires reviewed Vitest and browser selections",
        )
    require(
        all(git_blob(commit, path) == blob for path, blob in production.items()),
        "affected production blob differs from reviewed scope",
    )
    if any(path.startswith("desktop/managed/src/") for path in changes):
        require("managed" in selection, "managed production requires reviewed owning Node tests")
    cases = plan["case_sha256"]
    execution = plan["execution"]
    require(
        isinstance(execution, dict)
        and set(execution) == set(PROTECTED_FILES)
        and all(git_blob(commit, path) == blob for path, blob in execution.items()),
        "reviewed workflow and producer blobs differ from the tested source",
    )
    config_blob = git_blob(commit, "pyproject.toml")
    require(
        plan["config_blob"] == config_blob
        and isinstance(cases, dict)
        and set(cases) == set(selection)
        and all(isinstance(value, str) and DIGEST.fullmatch(value) for value in cases.values()),
        "reviewed configuration or test-case identity missing",
    )
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return Scope(workload, commit, tree, changes, production, config_blob, execution, selection, cases, digest)


def event_source(
    env: dict[str, str],
    get: Callable[[str], Any],
) -> tuple[WorkloadSource, str, dict[str, Any] | None]:
    """Resolve the actual Actions checkout and, for candidate dispatch, its open release PR."""
    event, ref = env["GITHUB_EVENT_NAME"], env["GITHUB_REF"]
    payload = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_bytes())
    commit = object_id("HEAD")
    require(commit == env["GITHUB_SHA"] and env["GITHUB_REPOSITORY"] == REPOSITORY, "unexpected CI checkout")
    dispatch_pr = None
    if event == "pull_request":
        pr = payload["pull_request"]
        head, base = pr["head"]["sha"], pr["base"]["sha"]
    else:
        head = commit
        base = env.get("BATCH_BASE") or payload.get("before") or head
        if event == "workflow_dispatch" and release_version(
            event, REPOSITORY, ref.removeprefix("refs/heads/"), payload
        ):
            dispatch_pr = dispatch_pull(head, ref.removeprefix("refs/heads/"), get)
            base = dispatch_pr["base"]["sha"]
    return WorkloadSource(event, REPOSITORY, ref, payload, base, head, raw_diff(base, commit)), commit, dispatch_pr


def current_scope() -> CandidatePlan | Scope:
    """Read the protected plan registry and resolve this workload's source."""

    def get(path: str) -> Any:  # noqa: ANN401 - GitHub response is checked against the source contract
        return json.loads(command(["gh", "api", f"repos/{REPOSITORY}/{path}"]))

    source, commit, dispatch_pr = event_source(dict(os.environ), get)
    return resolve(source, commit, os.environ.get(PLAN_VARIABLE, ""), dispatch_pr)


def main() -> None:
    """Write routing hints or print a reviewer's advisory affected selection."""
    if sys.argv[1:2] == ["--suggest"] and len(sys.argv[2:]) == 1:
        from scripts.affected import Repo, classify  # noqa: PLC0415 - advisory mode only

        base = sys.argv[2]
        changes = changed_paths(raw_diff(base, object_id("HEAD")))
        result = classify(Repo(Path.cwd()), list(changes))
        if result["full"] or "ALL" in (*result["pytest"], *result["vitest"]):
            result = {"files": list(changes), "reasons": result["reasons"], "requires_reviewed_manifest": True}
        print(json.dumps(result, sort_keys=True, indent=2))
        return
    scope = current_scope()
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        if isinstance(scope, Scope):
            matrix = json.dumps({"include": scope.matrix()}, separators=(",", ":"))
            output.write(f"focused_matrix={matrix}\n")
            output.write(f"focused_mode={scope.mode}\n")
            output.write(f"focused_base={scope.workload.base}\n")


if __name__ == "__main__":
    main()
