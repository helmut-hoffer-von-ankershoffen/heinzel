"""Pre-open planning binds the eventual GitHub merge without guessing its commit ID."""

import dataclasses
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
    monkeypatch.setattr(focused_plan, "selector_lanes", lambda _commit, _changed: ({"python": [TEST_PATH]}, "focused"))


def test_reviewed_plan_accepts_hyphenated_owning_web_paths(focused_plan, git_objects):
    paths = ["web/src/test/choices-standards.test.ts", "web/src/lib/pane-actions-standards.test.ts"]
    assert focused_plan._selections({"selection": {"web": paths}}, MERGE) == {"web": tuple(paths)}


@pytest.mark.parametrize(
    "path",
    [
        "-web/src/test/example.test.ts",
        "web/-option.test.ts",
        "web/src/../outside.test.ts",
        "web/src/test/example.test.ts;id",
        "tests/unit/example-test.py",
        "web/src/test/not-a-test.ts",
    ],
)
def test_reviewed_plan_rejects_unsafe_or_non_owning_hyphenated_paths(focused_plan, git_objects, path):
    with pytest.raises(focused_plan.CandidateError):
        focused_plan._selections({"selection": {"web": [path]}}, MERGE)


def test_a_pull_request_needs_no_manifest(focused_plan, git_objects):
    source, _plan = _input(focused_plan, "pull_request", "refs/pull/5/merge", focused_plan.PREOPEN_PR_REF)
    scope = focused_plan.resolve(source, MERGE, "{}")
    assert isinstance(scope, focused_plan.Scope)
    assert (scope.commit, scope.tree, scope.workload.ref) == (MERGE, TREE, "refs/pull/5/merge")
    assert scope.selection == {"python": (TEST_PATH,)}
    assert scope.production == {SOURCE_PATH: BLOB}
    assert (scope.full, scope.mode) == (False, "pr-scoped")
    assert scope.matrix() == [{"lane": "python", "os": "ubuntu-24.04", "python": "3.14.8", "kind": "pytest"}]


def test_only_canonical_python_lane_accepts_the_expanded_selection(focused_plan, git_objects):
    ids = [f"tests/unit/ci_test.py::test_case_{i}" for i in range(33)]
    assert focused_plan._selections({"selection": {"python": ids[:32]}}, MERGE)["python"] == tuple(ids[:32])
    with pytest.raises(focused_plan.CandidateError, match="broad focused lane python"):
        focused_plan._selections({"selection": {"python": ids}}, MERGE)
    for lane in ("python312", "python315", "macos", "macos-notifier", "web", "browser", "macos-browser"):
        with pytest.raises(focused_plan.CandidateError, match="broad focused lane"):
            focused_plan._selections({"selection": {lane: ids[:11]}}, MERGE)
    with pytest.raises(focused_plan.CandidateError, match="duplicate selection"):
        focused_plan._selections({"selection": {"python": ids[:31] + ids[:1]}}, MERGE)


@pytest.mark.parametrize(
    ("lanes", "reason"),
    [
        (None, "the selector selects the full set"),
        ({"web": ["web/src/x.test.ts"]}, "Python production without a Python selection"),
        ({"python": [TEST_PATH]}, None),
    ],
)
def test_the_selector_fails_open_to_the_full_set(focused_plan, git_objects, monkeypatch, lanes, reason):
    source, _plan = _input(focused_plan, "pull_request", "refs/pull/5/merge", focused_plan.PREOPEN_PR_REF)
    monkeypatch.setattr(focused_plan, "selector_lanes", lambda _c, _p: (lanes, reason or "focused"))
    scope = focused_plan.resolve(source, MERGE, "{}")
    if reason:
        assert (scope.full, scope.mode, scope.reason, scope.selection) == (True, "full", reason, {})
        assert scope.workload.jobs == (focused_plan.FULL_JOB,)
        assert scope.matrix() == []
    else:
        assert not scope.full


def test_a_full_contract_change_keeps_the_consumers_lane(focused_plan, git_objects, monkeypatch):
    path = "src/heinzel/heads/api/_routes.py"
    diff = f":100644 100644 {BLOB} {BLOB} M\0{path}\0".encode()
    source, _plan = _input(focused_plan, "pull_request", "refs/pull/5/merge", focused_plan.PREOPEN_PR_REF)
    source = dataclasses.replace(source, raw_diff=diff)
    monkeypatch.setattr(focused_plan, "selector_lanes", lambda _c, _p: (None, "e2e lane(s) cli have no focused lane"))
    scope = focused_plan.resolve(source, MERGE, "{}")
    assert scope.full
    assert scope.selection == {"consumers": ("plugins/main",)}


def test_a_wrong_merge_parent_is_refused(focused_plan, git_objects, monkeypatch):
    source, _plan = _input(focused_plan, "pull_request", "refs/pull/5/merge", focused_plan.PREOPEN_PR_REF)
    monkeypatch.setattr(focused_plan, "git", lambda *_args: f"{MERGE} {HEAD} {BASE}".encode())
    with pytest.raises(focused_plan.CandidateError, match="ordered base and head"):
        focused_plan.resolve(source, MERGE, "{}")


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
    source, _plan = _input(focused_plan, "pull_request", ref, focused_plan.PREOPEN_PR_REF)
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
    monkeypatch.setattr(focused_plan, "selector_lanes", lambda _c, _p: ({"python": [TEST_PATH]}, "focused"))

    def resolve_event():
        actual, commit, dispatch_pr, unproven = focused_plan.event_source(
            env, lambda _: pytest.fail("unexpected API read")
        )
        assert actual.payload["pull_request"]["base"]["sha"] == reported
        assert unproven is None
        return focused_plan.resolve(actual, commit, "{}", dispatch_pr)

    if error:
        with pytest.raises(focused_plan.CandidateError, match=error):
            resolve_event()
    else:
        scope = resolve_event()
        assert scope.workload.base == BASE
        assert scope.commit == MERGE


def test_preopen_widening_ref_is_accepted_only_for_a_pull_request(focused_plan, git_objects):
    source, plan = _input(focused_plan, "push", "refs/heads/main", focused_plan.PREOPEN_PR_REF)
    with pytest.raises(focused_plan.CandidateError, match="widening plan does not apply"):
        focused_plan.resolve(source, HEAD, json.dumps({f"push:{HEAD}": plan}))


def test_a_main_push_needs_no_manifest_and_a_tree_widening_still_applies(focused_plan, git_objects):
    source, plan = _input(focused_plan, "push", "refs/heads/main", "refs/heads/main")
    scope = focused_plan.resolve(source, HEAD, "{}")
    assert (scope.workload.head, scope.workload.base, scope.tree) == (HEAD, BASE, TREE)
    plan["selection"] = {"web": ["web/src/x.test.ts"]}
    widened = focused_plan.resolve(source, HEAD, json.dumps({f"push-tree:{TREE}": plan}))
    assert set(widened.selection) == {"python", "web"}


def test_a_tree_widening_does_not_apply_to_a_pr(focused_plan, git_objects):
    source, plan = _input(focused_plan, "pull_request", "refs/pull/5/merge", focused_plan.PREOPEN_PR_REF)
    plan["selection"] = {"web": ["web/src/x.test.ts"]}
    scope = focused_plan.resolve(source, MERGE, json.dumps({f"push-tree:{TREE}": plan}))
    assert set(scope.selection) == {"python"}


@pytest.mark.parametrize(
    ("green_runs", "batch", "expected", "unproven"),
    [
        ([("9" * 40, True)], None, "9" * 40, None),  # the last green commit, not the previous push
        ([("8" * 40, False), ("9" * 40, True)], None, "9" * 40, None),  # a run whose CI OK did not pass is skipped
        ([("9" * 40, True)], "7" * 40, "7" * 40, None),  # an older batch base covers more
        ([], None, "6" * 40, "no green main run before this push"),  # nothing green: the full set
        ([("5" * 40, True), ("9" * 40, True)], None, "9" * 40, None),  # CI OK alone is not accepted by the judge
    ],
)
def test_a_main_push_compares_against_the_last_green_main(  # noqa: PLR0913, PLR0917 - independent observations
    focused_plan, monkeypatch, tmp_path, green_runs, batch, expected, unproven
):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"before": "6" * 40}))
    env = {
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_SHA": HEAD,
        "GITHUB_REPOSITORY": focused_plan.REPOSITORY,
    } | ({"BATCH_BASE": batch} if batch else {})
    order = ["7" * 40, "8" * 40, "9" * 40, "5" * 40, HEAD]  # oldest first, one line of history

    def git(*args):
        if args[:2] == ("merge-base", "--is-ancestor"):
            if order.index(args[2]) < order.index(args[3]):
                return b""
            raise CalledProcessError(1, args)
        if args[0] == "diff-tree":
            return DIFF
        raise AssertionError(args)

    runs = [{"id": index, "head_sha": sha} for index, (sha, _ok) in enumerate(green_runs, 1)]
    ok = {index: passed for index, (_sha, passed) in enumerate(green_runs, 1)}

    def get(path):
        if path.startswith("actions/workflows/ci.yml/runs?branch=main&event=push&status=success"):
            return {"workflow_runs": runs}
        if path.startswith("commits/"):  # the main judge accepted every green run here except "5" * 40
            accepted = "success" if not path.startswith("commits/" + "5" * 40) else "failure"
            return [{"context": focused_plan.MAIN_PROOF_CONTEXT, "state": accepted}]
        index = int(path.split("/")[2])
        return {"jobs": [{"name": "CI OK", "conclusion": "success" if ok[index] else "failure"}]}

    monkeypatch.setattr(focused_plan, "object_id", lambda _ref: HEAD)
    monkeypatch.setattr(focused_plan, "git", git)
    source, commit, _dispatch, reason = focused_plan.event_source(env, get)
    assert (source.base, commit, reason) == (expected, HEAD, unproven)


def test_reviewed_plan_accepts_the_whole_vitest_suite_alone_in_the_web_lane(focused_plan, git_objects, monkeypatch):
    blobs = []
    monkeypatch.setattr(focused_plan, "git_blob", lambda *args: blobs.append(args))
    selection = focused_plan._selections({"selection": {"web": [focused_plan.VITEST_ALL]}}, MERGE)
    assert selection == {"web": ("web/ALL",)}
    assert blobs == []  # the sentinel names no file to pin


@pytest.mark.parametrize(
    "selection",
    [
        {"web": ["web/ALL", "web/src/test/choices-standards.test.ts"]},
        {"web": ["web/src/test/choices-standards.test.ts", "web/ALL"]},
        {"web": ["web/ALL", "web/ALL"]},
        {"python": ["web/ALL"]},
        {"browser": ["web/ALL"]},
        {"web": ["ALL"]},
    ],
)
def test_reviewed_plan_refuses_the_whole_vitest_suite_mixed_or_outside_the_web_lane(
    focused_plan, git_objects, selection
):
    with pytest.raises(focused_plan.CandidateError):
        focused_plan._selections({"selection": selection}, MERGE)


def test_reviewed_plan_accepts_owning_desktop_tests_and_the_whole_desktop_suite(focused_plan, git_objects):
    paths = ["desktop/test/shutdown.test.js", "desktop/test/window.test.js"]
    assert focused_plan._selections({"selection": {"desktop": paths}}, MERGE) == {"desktop": tuple(paths)}
    whole = focused_plan._selections({"selection": {"desktop": [focused_plan.DESKTOP_ALL]}}, MERGE)
    assert whole == {"desktop": ("desktop/ALL",)}
    assert focused_plan.LANES["desktop"] == ("ubuntu-24.04", "3.14", "node")


@pytest.mark.parametrize(
    "selection",
    [
        {"desktop": ["desktop/src/shutdown.js"]},
        {"desktop": ["desktop/managed/test/backend.test.ts"]},
        {"desktop": ["desktop/test/shutdown.test.ts"]},
        {"desktop": ["desktop/ALL", "desktop/test/window.test.js"]},
        {"desktop": ["web/ALL"]},
        {"web": ["desktop/ALL"]},
        {"managed": ["desktop/test/window.test.js"]},
    ],
)
def test_reviewed_plan_refuses_desktop_selections_outside_the_desktop_suite(focused_plan, git_objects, selection):
    with pytest.raises(focused_plan.CandidateError):
        focused_plan._selections({"selection": selection}, MERGE)


@pytest.mark.parametrize(
    ("path", "selection", "full"),
    [
        ("desktop/src/main.js", {"python": [TEST_PATH]}, True),
        ("desktop/test/window.test.js", {"python": [TEST_PATH]}, True),
        ("desktop/src/main.js", {"python": [TEST_PATH], "desktop": ["desktop/ALL"]}, False),
        ("desktop/README.md", {"python": [TEST_PATH]}, False),
    ],
)
def test_a_desktop_change_requires_the_desktop_unit_suite(  # noqa: PLR0913, PLR0917 - one case per row
    focused_plan, git_objects, monkeypatch, path, selection, full
):
    diff = DIFF + f":100644 100644 {BLOB} {BLOB} M\0{path}\0".encode()
    source, _plan = _input(focused_plan, "pull_request", "refs/pull/5/merge", focused_plan.PREOPEN_PR_REF)
    source = dataclasses.replace(source, raw_diff=diff)
    monkeypatch.setattr(focused_plan, "selector_lanes", lambda _c, _p: (selection, "focused"))
    scope = focused_plan.resolve(source, MERGE, "{}")
    assert scope.full is full
    if full:
        assert scope.reason == "a desktop change without the desktop unit suite"


@pytest.mark.parametrize(
    ("paths", "prose"),
    [
        (["docs/guides/install.md", "README.md"], True),
        (["SECURITY.md", ".github/AGENTS.md"], True),
        (["CHANGELOG.md"], False),
        (["docs/install.sh"], False),
        (["README.md", "scripts/affected.py"], False),
        ([], False),
    ],
)
def test_prose_only_matches_the_workflows_prose_push_rule(focused_plan, paths, prose):
    assert focused_plan.prose_only(dict.fromkeys(paths, "M")) is prose
    workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "'^(docs/.*|.*\\.md)$'" in workflow
    assert "'(^|/)CHANGELOG\\.md$|^docs/.*\\.sh$'" in workflow
