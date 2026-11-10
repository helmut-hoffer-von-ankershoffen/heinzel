"""Pre-open planning binds the eventual GitHub merge without guessing its commit ID."""

import hashlib
import json

import pytest

from scripts import focused_plan
from scripts.ci_workload import WorkloadSource
from scripts.release_candidate import REPOSITORY, CandidateError

pytestmark = pytest.mark.unit
BASE, HEAD, MERGE, TREE, BLOB = (digit * 40 for digit in "abcde")
SOURCE_PATH = "src/heinzel/new.py"
TEST_PATH = "tests/unit/new_test.py"
DIFF = f":000000 100644 {'0' * 40} {BLOB} A\0{SOURCE_PATH}\0".encode()


def _input(event, ref, manifest_ref):
    payload = {"pull_request": {"number": 5, "base": {"sha": BASE}, "head": {"sha": HEAD}}}
    source = WorkloadSource(event, REPOSITORY, ref, payload, BASE, HEAD, DIFF)
    plan = {
        "schema": 1,
        "event": event,
        "ref": manifest_ref,
        "base": BASE,
        "head": HEAD,
        "tree": TREE,
        "diff_sha256": hashlib.sha256(DIFF).hexdigest(),
        "changed": {SOURCE_PATH: "A"},
        "mode": "pr-scoped",
        "config_blob": BLOB,
        "execution": dict.fromkeys(focused_plan.PROTECTED_FILES, BLOB),
        "production": {SOURCE_PATH: BLOB},
        "selection": {"python": [TEST_PATH]},
        "case_sha256": {"python": "f" * 64},
    }
    return source, plan


@pytest.fixture
def git_objects(monkeypatch):
    monkeypatch.setattr(focused_plan, "git_blob", lambda *_args: BLOB)
    monkeypatch.setattr(focused_plan, "object_id", lambda _ref: TREE)
    monkeypatch.setattr(focused_plan, "git", lambda *_args: f"{MERGE} {BASE} {HEAD}".encode())


def test_reviewed_tree_can_be_provisioned_before_pr_number_and_merge_sha_exist(git_objects):
    source, plan = _input("pull_request", "refs/pull/5/merge", focused_plan.PREOPEN_PR_REF)
    scope = focused_plan.resolve(source, MERGE, json.dumps({f"pull_request:{HEAD}": plan}))
    assert isinstance(scope, focused_plan.Scope)
    assert scope.commit == MERGE
    assert scope.tree == TREE
    assert scope.workload.ref == "refs/pull/5/merge"


@pytest.mark.parametrize("defect", ["tree", "parents", "commit", "production", "ref"])
def test_plan_cannot_cover_other_source_or_omit_new_production(git_objects, monkeypatch, defect):
    source, plan = _input("pull_request", "refs/pull/5/merge", focused_plan.PREOPEN_PR_REF)
    if defect == "parents":
        monkeypatch.setattr(focused_plan, "git", lambda *_args: f"{MERGE} {HEAD} {BASE}".encode())
    else:
        plan[defect] = {"tree": BLOB, "commit": BLOB, "production": {}, "ref": "refs/pull/9/merge"}[defect]
    with pytest.raises(CandidateError, match=r".+"):
        focused_plan.resolve(source, MERGE, json.dumps({f"pull_request:{HEAD}": plan}))


def test_preopen_planning_ref_is_accepted_only_for_a_pull_request(git_objects):
    source, plan = _input("push", "refs/heads/main", focused_plan.PREOPEN_PR_REF)
    with pytest.raises(CandidateError, match="reviewed ref"):
        focused_plan.resolve(source, HEAD, json.dumps({f"push:{HEAD}": plan}))
