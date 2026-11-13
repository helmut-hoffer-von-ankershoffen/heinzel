# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Resolve the administrator-reviewed workload for the exact Git tree tested by CI."""

from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from subprocess import CalledProcessError
from typing import TYPE_CHECKING, Any

# The isolated workflow launcher supplies only this exact checkout's modules.
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_workload import CandidatePlan, FocusedPlan, WorkloadSource, plan_workload
from scripts.focused_job import MAX_IDS, PYTHON_CI_MAX_IDS, TOKEN, VITEST_ALL
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

    Selector = Callable[[str, list[str]], tuple[dict[str, list[str]] | None, str]]

PATH = re.compile(r"[A-Za-z0-9_.@+-]+(?:/[A-Za-z0-9_.@+-]+)*")
LANES = {
    "python": ("ubuntu-24.04", "3.14.8", "pytest"),
    "python312": ("ubuntu-24.04", "3.12", "pytest"),
    "python315": ("ubuntu-24.04", "3.15", "pytest"),
    "macos": ("macos-latest", "3.14", "pytest"),
    "macos-notifier": ("macos-latest", "3.14", "pytest"),
    "desktop": ("ubuntu-24.04", "3.14", "node"),
    # Advisory until the plugins' lock-by-contract toolkit release: see ADVISORY_LANES.
    "consumers": ("ubuntu-24.04", "3.14", "consumer"),
    "web": ("ubuntu-24.04", "3.14", "vitest"),
    "browser": ("ubuntu-24.04", "3.14", "e2e"),
    "e2e-web-1": ("ubuntu-24.04", "3.14", "e2e"),  # a whole browser e2e suite, when "browser" would exceed its limit
    "e2e-web-2": ("ubuntu-24.04", "3.14", "e2e"),
    "macos-browser": ("macos-latest", "3.14", "e2e"),
}
# The whole desktop unit suite (desktop/test/*.test.js); like VITEST_ALL it names no file and stands alone.
DESKTOP_ALL = "desktop/ALL"
# Every public plugin's contract tests at heinzel-plugins main, against this tree's wheel, serially.
CONSUMERS_MAIN = "plugins/main"
WHOLE_SUITES = {
    "web": VITEST_ALL,
    "desktop": DESKTOP_ALL,
    "consumers": CONSUMERS_MAIN,
    "e2e-web-1": "tests/e2e/web-1",
    "e2e-web-2": "tests/e2e/web-2",
}
BROWSER_LANES = frozenset({"browser", "macos-browser", "e2e-web-1", "e2e-web-2"})
# Lanes that report without blocking. TODO(ADR 0149): make "consumers" required once the
# plugins' lock-by-contract toolkit release lands; until then a Core dependency edit fails it on every plugin.
ADVISORY_LANES = frozenset({"consumers"})
PREFIXES = {
    "pytest": "tests/",
    "vitest": "web/",
    "e2e": "tests/e2e/",
    "node": "desktop/test/",
    "consumer": "plugins/",
}
# The job whose success stands for the full set of a merge-tier run (the candidate-only jobs, on a PR or main push).
FULL_JOB = "CI OK"
GREEN_RUNS = 30
# The plugin contract: a change here runs the plugin consumers lane (ci.yml's "contracts" paths).
CONTRACTS = re.compile(
    r"^(?:src/heinzel/kernel/|src/heinzel/__init__\.py$|src/heinzel/heads/|src/heinzel/modules/system/ui/|web/src/plugins/)"
)
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
SLOT_VARIABLES = tuple(f"FOCUSED_CI_PLANS_V2_{index}" for index in range(4))
MAX_SLOT = 40_000
MAX_SLOT_DECODED = 1024 * 1024
MAX_SLOT_ENTRIES = 256
REGISTRY_KEY = re.compile(r"(?:pull_request|push|push-tree):[0-9a-f]{40}")
MAX_DIFF = 1024 * 1024
MAX_COVERED_FILES = 100
RAW_FIELDS = 5
LS_TREE_FIELDS = 2
MERGE_FIELDS = 3


def registry_bytes(value: Any) -> bytes:  # noqa: ANN401 - canonical JSON boundary
    """Serialize without changing the original manifest's canonical digest."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _registry_json(raw: str | bytes) -> dict[str, Any]:
    """Reject ambiguous, non-finite, malformed or excessively nested JSON.

    Raises:
        CandidateError: The registry is not an unambiguous JSON object.
    """
    try:
        value = json.loads(raw, object_pairs_hook=unique_object)
        require(isinstance(value, dict), "focused registry must be an object")
        registry_bytes(value)  # Reject NaN/Infinity even in an unselected entry.
    except (ValueError, UnicodeError, RecursionError) as exc:
        message = "invalid focused registry JSON"
        raise CandidateError(message) from exc
    return value


def _slot_entries(registry: dict[str, Any]) -> None:
    require(len(registry) <= MAX_SLOT_ENTRIES, "focused slot has too many entries")
    for key, plan in registry.items():
        require(
            bool(REGISTRY_KEY.fullmatch(key)) and isinstance(plan, dict),
            "invalid focused slot entry",
        )
        require(len(registry_bytes({key: plan})) <= MAX_PLAN, "focused slot entry exceeds the original plan limit")


def decode_slot(value: str) -> dict[str, Any]:
    """Decode one bounded, complete gzip member without allocating unbounded output.

    Raises:
        CandidateError: The slot is malformed or exceeds a transport bound.
    """
    require(len(value.encode()) <= MAX_SLOT, "focused slot exceeds its encoded limit")
    if not value:
        return {}
    envelope = _registry_json(value)
    require(
        set(envelope) == {"schema", "codec", "bytes", "sha256", "data"}
        and type(envelope["schema"]) is int
        and envelope["schema"] == 1
        and envelope["codec"] == "gzip+base64"
        and type(envelope["bytes"]) is int
        and 0 < envelope["bytes"] <= MAX_SLOT_DECODED
        and isinstance(envelope["sha256"], str)
        and DIGEST.fullmatch(envelope["sha256"])
        and isinstance(envelope["data"], str),
        "invalid focused slot envelope",
    )
    try:
        compressed = base64.b64decode(envelope["data"], validate=True)
        decoder = zlib.decompressobj(wbits=31)
        raw = decoder.decompress(compressed, envelope["bytes"] + 1)
    except (ValueError, binascii.Error, zlib.error) as exc:
        message = "invalid focused slot compression"
        raise CandidateError(message) from exc
    require(
        len(raw) == envelope["bytes"]
        and decoder.eof
        and not decoder.unused_data
        and not decoder.unconsumed_tail
        and hashlib.sha256(raw).hexdigest() == envelope["sha256"],
        "focused slot length, stream or digest mismatch",
    )
    registry = _registry_json(raw)
    require(raw == registry_bytes(registry), "focused slot JSON must be canonical")
    _slot_entries(registry)
    return registry


def encode_slot(registry: dict[str, Any]) -> str:
    """Prepare deterministic transport bytes; this does not authorize any manifest."""
    _slot_entries(registry)
    raw = registry_bytes(registry)
    require(len(raw) <= MAX_SLOT_DECODED, "focused slot exceeds its decoded limit")
    value = registry_bytes(
        {
            "schema": 1,
            "codec": "gzip+base64",
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "data": base64.b64encode(gzip.compress(raw, compresslevel=9, mtime=0)).decode("ascii"),
        }
    ).decode()
    require(len(value.encode()) <= MAX_SLOT, "focused slot exceeds its encoded limit")
    return value


def registry_slots(env: dict[str, str]) -> tuple[str, ...]:
    """Read only the four fixed optional transport variables."""
    return tuple(env.get(name, "") for name in SLOT_VARIABLES)


def plan_registry(legacy: str, slots: tuple[str, ...] = ()) -> dict[str, Any]:
    """Read all transports, refusing conflicting duplicates even when unselected."""
    require(len(legacy.encode()) <= MAX_PLAN, "focused plan registry exceeds its supported size")
    require(len(slots) <= len(SLOT_VARIABLES), "too many focused registry slots")
    registry = _registry_json(legacy or "{}")
    for slot in slots:
        for key, plan in decode_slot(slot).items():
            require(
                key not in registry or registry_bytes(registry[key]) == registry_bytes(plan),
                "conflicting focused registry entries",
            )
            registry[key] = plan
    return registry


def selected_registry(legacy: str, slots: tuple[str, ...], key: str) -> str:
    """Keep the protected judge's exact PR lookup bounded by the original plan limit."""
    registry = plan_registry(legacy, slots)
    selected = registry_bytes({key: registry[key]} if key in registry else {}).decode()
    require(len(selected.encode()) <= MAX_PLAN, "focused slot entry exceeds the original plan limit")
    return selected


def append_slot(legacy: str, slots: tuple[str, ...], additions: dict[str, Any]) -> tuple[int, str] | None:
    """Propose one append-only variable update, keeping an admission pair atomic.

    Callers must first validate every manifest against trusted source, then reread
    all inputs under their writer lock and verify exact readback after one write.
    No registry or remote state is changed here.

    Raises:
        CandidateError: Inputs conflict, are incomplete or exhaust bounded storage.
    """
    require(len(slots) == len(SLOT_VARIABLES) and bool(additions), "incomplete focused append inputs")
    _slot_entries(additions)
    current = plan_registry(legacy, slots)
    for key, plan in additions.items():
        require(
            key not in current or registry_bytes(current[key]) == registry_bytes(plan),
            "focused append cannot replace an existing manifest",
        )
    if all(key in current for key in additions):
        return None
    require(not any(key in current for key in additions), "focused admission is only partially present")
    for index, slot in enumerate(slots):
        proposed = {**decode_slot(slot), **additions}
        try:
            encoded = encode_slot(proposed)
        except CandidateError:
            continue
        return index, encoded
    message = "all focused registry slots are full; existing proofs cannot be removed or moved"
    raise CandidateError(message)


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
    full: bool = False
    reason: str = ""

    @property
    def mode(self) -> str:
        """Describe the workload: the full set, or the applicability of the scoped Python coverage policy."""
        if self.full:
            return "full"
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


def desktop_unit(path: str) -> bool:
    """Recognize the desktop app's own files, which its Node unit suite (desktop/test) tests."""
    return path.startswith("desktop/") and not path.startswith("desktop/managed/") and not path.endswith(".md")


def _path(path: object) -> bool:
    return isinstance(path, str) and PATH.fullmatch(path) is not None and not {".", ".."}.intersection(path.split("/"))


def raw_diff(base: str, commit: str) -> bytes:
    """Get the exact reviewable Git diff with rename halves represented independently."""
    require(valid_sha(base) and valid_sha(commit), "missing exact Git identities")
    return git("diff-tree", "--no-commit-id", "-r", "--raw", "--no-abbrev", "--no-renames", "-z", base, commit)


def pr_merge_base(commit: str, head: str) -> str:
    """Read the tested base from GitHub's ordered two-parent merge."""
    require(valid_sha(commit) and valid_sha(head), "missing exact Git identities")
    parents = git("rev-list", "--parents", "-n", "1", commit).decode().split()
    require(
        len(parents) == MERGE_FIELDS and parents[0] == commit and valid_sha(parents[1]) and parents[2] == head,
        "GitHub test merge does not bind ordered base and head",
    )
    return parents[1]


def require_pr_base_ancestor(reported: str, tested: str) -> None:
    """Allow a cached GitHub base SHA only from the tested base's history.

    Raises:
        CandidateError: The reported SHA is invalid or cannot be proved to precede the tested base.
    """
    require(valid_sha(reported) and valid_sha(tested), "missing exact PR base identities")
    if reported != tested:
        try:
            git("merge-base", "--is-ancestor", reported, tested)
        except CalledProcessError as exc:
            message = "reported PR base is outside the tested main history"
            raise CandidateError(message) from exc


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
        prefix = PREFIXES[kind]
        limit = PYTHON_CI_MAX_IDS if lane == "python" else MAX_IDS
        require(isinstance(tests, list) and 0 < len(tests) <= limit, f"empty or broad focused lane {lane}")
        require(len(set(tests)) == len(tests), f"duplicate selection in {lane}")
        whole = [owner for owner, token in WHOLE_SUITES.items() if token in tests]
        if whole:
            # A whole suite: only alone, only in its own lane; it names no file, so no blob to pin.
            require(whole == [lane] and tests == [WHOLE_SUITES[lane]], "a whole suite is only its own lane, alone")
            selected[lane] = (WHOLE_SUITES[lane],)
            continue
        for test in tests:
            path = test.split("::", 1)[0]
            require(
                TOKEN.fullmatch(test) and _path(path) and path.startswith(prefix), f"invalid focused test in {lane}"
            )
            require(
                (kind == "vitest" and path == test and re.search(r"\.(?:test|spec)\.[cm]?[jt]sx?$", path))
                or (kind == "node" and path == test and path.endswith(".test.js"))
                or (kind in {"pytest", "e2e"} and path.endswith("_test.py")),
                "focused selections require owning test files or exact test node IDs",
            )
            require(kind != "pytest" or not path.startswith("tests/e2e/"), "browser tests require their owning lane")
            git_blob(commit, path)
        selected[lane] = tuple(tests)
    return selected


def resolve(  # noqa: PLR0913 - independent source, dispatch, base and transport boundaries
    source: WorkloadSource,
    commit: str,
    manifests: str,
    dispatch_pr: dict[str, Any] | None = None,
    *,
    pr_base_limit: str | None = None,
    slots: tuple[str, ...] = (),
    select: Selector | None = None,
) -> CandidatePlan | Scope:
    """Resolve an exact release candidate (release tier) or the selected merge-tier workload.

    Merge tier (pull requests and main pushes): the in-repo selector maps the exact diff to focused lanes and fails
    open to the full set. A manifest in the plan registry may only widen that selection, never narrow it.

    Raises:
        CandidateError: The source is not exact, or no supported workload applies.
    """
    # Full candidate recognition is deliberately independent of the merge-tier selection.
    if release_version(source.event, source.repository, source.ref.removeprefix("refs/heads/"), source.payload):
        workload = plan_workload(source, dispatch_pr=dispatch_pr)
        if isinstance(workload, CandidatePlan):
            return workload
        message = "canonical candidate source did not resolve to the full inventory"
        raise CandidateError(message)
    require(valid_sha(commit), "missing exact tested checkout")
    if source.event == "pull_request":
        require(pr_merge_base(commit, source.head) == source.base, "GitHub test merge differs from the tested base")
        limit = pr_base_limit or source.base
        require_pr_base_ancestor(source.base, limit)
        require_pr_base_ancestor(source.payload["pull_request"]["base"]["sha"], limit)
    else:
        require(commit == source.head, "checkout differs from exact source head")
    changes = changed_paths(source.raw_diff) if source.raw_diff else {}
    lanes, reason = (select or selector_lanes)(commit, list(changes)) if changes else (None, "no exact change")
    widening = registry_widening(plan_registry(manifests, slots), source, commit)
    production = {
        path: git_blob(commit, path) for path in sorted(changes) if _production(path) and changes[path] != "D"
    }
    selection: dict[str, tuple[str, ...]] | None = None
    if lanes is not None:
        selection = _selections({"selection": _union(lanes, widening)}, commit)
        reason = _unmet(changes, production, selection) or reason
        if reason != "focused":
            selection = None
    full = selection is None
    workload = plan_workload(
        source,
        focused=FocusedPlan(
            source.event,
            source.ref,
            source.base,
            source.head,
            hashlib.sha256(source.raw_diff).hexdigest(),
            (FULL_JOB,) if full else tuple(f"Focused tests ({lane})" for lane in selection or {}),
            full=full,
        ),
    )
    if not isinstance(workload, FocusedPlan):
        message = "ordinary source did not resolve to a merge-tier workload"
        raise CandidateError(message)
    tree = object_id(f"{commit}^{{tree}}")
    config_blob = git_blob(commit, "pyproject.toml")
    # The full set runs the candidate jobs, whose consumer matrix stays release-only: contract changes keep the lane.
    contracts = {"consumers": (CONSUMERS_MAIN,)} if any(CONTRACTS.match(path) for path in changes) else {}
    chosen = selection if selection is not None else contracts
    descriptor = {
        "schema": 2,
        "event": source.event,
        "ref": source.ref,
        "base": source.base,
        "head": source.head,
        "commit": commit,
        "tree": tree,
        "changed": changes,
        "production": production,
        "config_blob": config_blob,
        "selection": {lane: list(tests) for lane, tests in chosen.items()},
        "full": full,
        "reason": reason,
    }
    digest = hashlib.sha256(json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return Scope(workload, commit, tree, changes, production, config_blob, {}, chosen, {}, digest, full, reason)


def _union(lanes: dict[str, list[str]], widening: dict[str, list[str]]) -> dict[str, list[str]]:
    merged = {lane: list(tests) for lane, tests in lanes.items()}
    for lane, tests in widening.items():
        current = merged.get(lane, [])
        whole = WHOLE_SUITES.get(lane)
        if whole in current or whole in tests:
            merged[lane] = [whole]
        else:
            merged[lane] = sorted({*current, *tests})
    return merged


def _unmet(changes: dict[str, str], production: dict[str, str], selection: dict[str, tuple[str, ...]]) -> str | None:
    """The first lane requirement a selection misses; the caller then runs the full set (fail-open)."""
    if len(production) > MAX_COVERED_FILES:
        return f"{len(production)} production files exceed the scoped coverage bound of {MAX_COVERED_FILES}"
    if production and "python" not in selection:
        return "Python production without a Python selection"
    if any(web_production(path) for path in changes) and not ("web" in selection and BROWSER_LANES & selection.keys()):
        return "web production without Vitest and browser selections"
    if any(desktop_unit(path) for path in changes) and "desktop" not in selection:
        return "a desktop change without the desktop unit suite"
    return None


def registry_widening(registry: dict[str, Any], source: WorkloadSource, commit: str) -> dict[str, list[str]]:
    """An optional reviewed widening for this exact source: its lanes, which are only ever added."""
    plan = registry.get(f"{source.event}:{source.head}")
    if plan is None and source.event == "push" and source.ref == "refs/heads/main":
        plan = registry.get(f"push-tree:{object_id(f'{commit}^{{tree}}')}")
    if plan is None:
        return {}
    require(
        isinstance(plan, dict)
        and (plan.get("ref") == source.ref or (source.event == "pull_request" and plan.get("ref") == PREOPEN_PR_REF)),
        "a widening plan does not apply to the exact workflow event",
    )
    return {lane: list(tests) for lane, tests in _selections(plan, commit).items()}


def selector_lanes(commit: str, changed: list[str]) -> tuple[dict[str, list[str]] | None, str]:
    """Run this checkout's selector over the exact tested tree, as data.

    The tree is extracted to a scratch directory; only this checkout's own scripts/affected.py and
    scripts/related_tests.py run, so the judge on the default branch never executes the tested tree's selector.
    """
    scripts = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="focused-select-") as scratch:
        root = Path(scratch)
        with tarfile.open(fileobj=io.BytesIO(git("archive", "--format=tar", commit))) as archive:
            archive.extractall(root, filter="data")
        affected = _load(scripts / "affected.py", "focused_affected")
        related = _load(scripts / "related_tests.py", "focused_related_tests")
        related.ROOT = root
        repo = affected.Repo(root)
        repo.__dict__["related"] = related
        return affected.focused_selection(repo, changed)


def _load(path: Path, name: str) -> Any:  # noqa: ANN401 - a module object
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, f"cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def event_source(
    env: dict[str, str],
    get: Callable[[str], Any],
) -> tuple[WorkloadSource, str, dict[str, Any] | None, str | None]:
    """Resolve the actual Actions checkout, a candidate dispatch's open release PR, and why it must run in full."""
    event, ref = env["GITHUB_EVENT_NAME"], env["GITHUB_REF"]
    payload = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_bytes())
    commit = object_id("HEAD")
    require(commit == env["GITHUB_SHA"] and env["GITHUB_REPOSITORY"] == REPOSITORY, "unexpected CI checkout")
    dispatch_pr = None
    unproven = None
    if event == "pull_request":
        pr = payload["pull_request"]
        head = pr["head"]["sha"]
        base = pr["base"]["sha"] if release_version(event, REPOSITORY, "", payload) else pr_merge_base(commit, head)
    elif event == "push":
        head = commit
        green = last_green_main(get, head)
        if green is None:  # nothing proven green to compare against: fail open to the full set
            base = env.get("BATCH_BASE") or payload.get("before") or head
            unproven = "no green main run before this push"
        else:
            base = oldest_ancestor(env.get("BATCH_BASE") or green, green)
    else:
        head = commit
        base = env.get("BATCH_BASE") or payload.get("before") or head
        if event == "workflow_dispatch" and release_version(
            event, REPOSITORY, ref.removeprefix("refs/heads/"), payload
        ):
            dispatch_pr = dispatch_pull(head, ref.removeprefix("refs/heads/"), get)
            base = dispatch_pr["base"]["sha"]
    source = WorkloadSource(event, REPOSITORY, ref, payload, base, head, raw_diff(base, commit))
    return source, commit, dispatch_pr, unproven


def _is_ancestor(older: str, newer: str) -> bool:
    try:
        git("merge-base", "--is-ancestor", older, newer)
    except CalledProcessError:
        return False
    return True


def oldest_ancestor(first: str, second: str) -> str:
    """The older of two commits on one history, so a main run covers every push since either.

    Raises:
        CandidateError: The commits are not on one line of history.
    """
    require(valid_sha(first) and valid_sha(second), "invalid main base")
    if first == second or _is_ancestor(first, second):
        return first
    require(_is_ancestor(second, first), "main bases are not on one history")
    return second


def last_green_main(get: Callable[[str], Any], head: str) -> str | None:
    """The newest main commit before HEAD whose push run passed CI OK.

    Main pushes share one concurrency group, so a newer push can replace a pending run. Comparing against the last
    green commit, not the previous push, makes the tip's run cover the union of every replaced push.
    """
    runs = get(f"actions/workflows/ci.yml/runs?branch=main&event=push&status=success&per_page={GREEN_RUNS}")
    for run in runs.get("workflow_runs", []):
        sha = run.get("head_sha")
        if not (isinstance(sha, str) and valid_sha(sha)) or sha == head or not _is_ancestor(sha, head):
            continue
        jobs = get(f"actions/runs/{run['id']}/jobs?filter=latest&per_page=100").get("jobs", [])
        if any(job.get("name") == FULL_JOB and job.get("conclusion") == "success" for job in jobs):
            return sha
    return None


def current_scope() -> CandidatePlan | Scope:
    """Read the protected plan registry and resolve this workload's source."""

    def get(path: str) -> Any:  # noqa: ANN401 - GitHub response is checked against the source contract
        return json.loads(command(["gh", "api", f"repos/{REPOSITORY}/{path}"]))

    source, commit, dispatch_pr, unproven = event_source(dict(os.environ), get)
    return resolve(
        source,
        commit,
        os.environ.get(PLAN_VARIABLE, ""),
        dispatch_pr,
        slots=registry_slots(dict(os.environ)),
        select=(lambda _commit, _changed: (None, unproven)) if unproven else None,
    )


def main() -> None:
    """Write the merge-tier routing outputs, or print the selection for a base (--suggest BASE)."""
    if sys.argv[1:2] == ["--suggest"] and len(sys.argv[2:]) == 1:
        commit = object_id("HEAD")
        changes = changed_paths(raw_diff(object_id(f"{sys.argv[2]}^{{commit}}"), commit))
        lanes, reason = selector_lanes(commit, list(changes))
        print(json.dumps({"files": list(changes), "lanes": lanes, "full": lanes is None, "reason": reason}, indent=2))
        return
    scope = current_scope()
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        if isinstance(scope, Scope):
            matrix = json.dumps({"include": scope.matrix()}, separators=(",", ":"))
            output.write(f"focused_matrix={matrix}\n")
            output.write(f"focused_mode={scope.mode}\n")
            output.write(f"focused_base={scope.workload.base}\n")
            output.write(f"focused_full={'true' if scope.full else 'false'}\n")
    if isinstance(scope, Scope) and (summary := os.environ.get("GITHUB_STEP_SUMMARY")):
        with Path(summary).open("a", encoding="utf-8") as out:
            lanes = ", ".join(f"{lane} ({len(tests)})" for lane, tests in scope.selection.items()) or "none"
            out.write(f"### Merge-tier selection: {scope.mode}\n\nReason: {scope.reason}\n\nLanes: {lanes}\n")


if __name__ == "__main__":
    main()
