"""Pre-open planning binds the eventual GitHub merge without guessing its commit ID."""

import hashlib
import json
from pathlib import Path
from subprocess import CalledProcessError

import pytest

pytestmark = pytest.mark.unit
BASE, HEAD, MERGE, TREE, BLOB = (digit * 40 for digit in "abcde")
SOURCE_PATH = "src/heinzel/new.py"
TEST_PATH = "tests/unit/new_test.py"
DIFF = f":000000 100644 {'0' * 40} {BLOB} A\0{SOURCE_PATH}\0".encode()


@pytest.fixture
def focused_plan(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from scripts import focused_plan

    return focused_plan


def _input(focused_plan, event, ref, manifest_ref):
    payload = {
        "pull_request": {
            "number": 5,
            "base": {"sha": BASE, "ref": "main", "repo": {"full_name": focused_plan.REPOSITORY}},
            "head": {"sha": HEAD},
        },
    }
    source = focused_plan.WorkloadSource(event, focused_plan.REPOSITORY, ref, payload, BASE, HEAD, DIFF)
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
def git_objects(focused_plan, monkeypatch):
    monkeypatch.setattr(focused_plan, "git_blob", lambda *_args: BLOB)
    monkeypatch.setattr(focused_plan, "object_id", lambda _ref: TREE)
    monkeypatch.setattr(focused_plan, "git", lambda *_args: f"{MERGE} {BASE} {HEAD}".encode())


def test_reviewed_tree_can_be_provisioned_before_pr_number_and_merge_sha_exist(focused_plan, git_objects):
    source, plan = _input(focused_plan, "pull_request", "refs/pull/5/merge", focused_plan.PREOPEN_PR_REF)
    scope = focused_plan.resolve(source, MERGE, json.dumps({f"pull_request:{HEAD}": plan}))
    assert isinstance(scope, focused_plan.Scope)
    assert scope.commit == MERGE
    assert scope.tree == TREE
    assert scope.workload.ref == "refs/pull/5/merge"


@pytest.mark.parametrize(
    "defect", ["tree", "parents", "commit", "production", "ref", "base", "diff_sha256", "execution"]
)
def test_plan_cannot_cover_other_source_or_omit_new_production(focused_plan, git_objects, monkeypatch, defect):
    source, plan = _input(focused_plan, "pull_request", "refs/pull/5/merge", focused_plan.PREOPEN_PR_REF)
    if defect == "parents":
        monkeypatch.setattr(focused_plan, "git", lambda *_args: f"{MERGE} {HEAD} {BASE}".encode())
    else:
        plan[defect] = {
            "tree": BLOB,
            "commit": BLOB,
            "production": {},
            "ref": "refs/pull/9/merge",
            "base": BLOB,
            "diff_sha256": "0" * 64,
            "execution": dict.fromkeys(focused_plan.PROTECTED_FILES, HEAD),
        }[defect]
    with pytest.raises(focused_plan.CandidateError, match=r".+"):
        focused_plan.resolve(source, MERGE, json.dumps({f"pull_request:{HEAD}": plan}))


@pytest.mark.parametrize(
    ("defect", "error"),
    [
        ("none", ""),
        ("unrelated-base", "outside the tested main history"),
        ("future-base", "outside the tested main history"),
        ("wrong-base-ref", "event differs from the exact ref and source"),
        ("wrong-base-repo", "event differs from the exact ref and source"),
        ("wrong-pr-ref", "event differs from the exact ref and source"),
        ("wrong-head", "ordered base and head"),
        ("third-parent", "ordered base and head"),
        ("wrong-checkout", "unexpected CI checkout"),
    ],
)
def test_actions_checkout_supplies_exact_base_despite_cached_event_metadata(
    focused_plan, monkeypatch, tmp_path, defect, error
):
    ref = "refs/pull/5/merge"
    source, plan = _input(focused_plan, "pull_request", ref, focused_plan.PREOPEN_PR_REF)
    reported = "f" * 40 if defect != "future-base" else "1" * 40
    pr = source.payload["pull_request"]
    pr["base"]["sha"] = reported
    if defect == "wrong-base-ref":
        pr["base"]["ref"] = "other"
    elif defect == "wrong-base-repo":
        pr["base"]["repo"]["full_name"] = "someone/fork"
    elif defect == "wrong-head":
        pr["head"]["sha"] = reported
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(source.payload))
    env = {
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_REF": "refs/pull/9/merge" if defect == "wrong-pr-ref" else ref,
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_SHA": BLOB if defect == "wrong-checkout" else MERGE,
        "GITHUB_REPOSITORY": focused_plan.REPOSITORY,
    }
    parents = f"{MERGE} {BASE} {HEAD}"
    if defect == "third-parent":
        parents += f" {reported}"

    def git(*args):
        if args == ("merge-base", "--is-ancestor", reported, BASE) and defect in {"unrelated-base", "future-base"}:
            raise CalledProcessError(1, args)
        return {
            ("rev-list", "--parents", "-n", "1", MERGE): parents.encode(),
            ("merge-base", "--is-ancestor", reported, BASE): b"",
            ("diff-tree", "--no-commit-id", "-r", "--raw", "--no-abbrev", "--no-renames", "-z", BASE, MERGE): DIFF,
        }[args]

    monkeypatch.setattr(focused_plan, "object_id", lambda ref: MERGE if ref == "HEAD" else TREE)
    monkeypatch.setattr(focused_plan, "git_blob", lambda *_args: BLOB)
    monkeypatch.setattr(focused_plan, "git", git)

    def resolve_event():
        actual, commit, dispatch_pr = focused_plan.event_source(env, lambda _: pytest.fail("unexpected API read"))
        assert actual.payload["pull_request"]["base"]["sha"] == reported
        return focused_plan.resolve(actual, commit, json.dumps({f"pull_request:{HEAD}": plan}), dispatch_pr)

    if error:
        with pytest.raises(focused_plan.CandidateError, match=error):
            resolve_event()
    else:
        scope = resolve_event()
        assert scope.workload.base == BASE
        assert scope.commit == MERGE


def test_preopen_planning_ref_is_accepted_only_for_a_pull_request(focused_plan, git_objects):
    source, plan = _input(focused_plan, "push", "refs/heads/main", focused_plan.PREOPEN_PR_REF)
    with pytest.raises(focused_plan.CandidateError, match="reviewed ref"):
        focused_plan.resolve(source, HEAD, json.dumps({f"push:{HEAD}": plan}))


def test_main_squash_can_preprovision_the_exact_base_and_result_tree(focused_plan, git_objects):
    source, plan = _input(focused_plan, "push", "refs/heads/main", "refs/heads/main")
    del plan["head"]
    scope = focused_plan.resolve(source, HEAD, json.dumps({f"push-tree:{TREE}": plan}))
    assert isinstance(scope, focused_plan.Scope)
    assert scope.workload.head == HEAD
    assert scope.workload.base == BASE
    assert scope.tree == TREE
    plan["base"] = BLOB
    with pytest.raises(focused_plan.CandidateError, match="exact event, ref and diff"):
        focused_plan.resolve(source, HEAD, json.dumps({f"push-tree:{TREE}": plan}))


def test_tree_registry_key_does_not_replace_exact_head_for_a_pr(focused_plan, git_objects):
    source, plan = _input(focused_plan, "pull_request", "refs/pull/5/merge", focused_plan.PREOPEN_PR_REF)
    with pytest.raises(focused_plan.CandidateError, match="independently reviewed scope manifest"):
        focused_plan.resolve(source, MERGE, json.dumps({f"push-tree:{TREE}": plan}))
