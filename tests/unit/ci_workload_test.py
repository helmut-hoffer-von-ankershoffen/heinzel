"""Exercise dormant workload routing with exact detached scope and current release contracts."""

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
BASE, HEAD = "a" * 40, "b" * 40
RAW_DIFF = b":100644 100644 " + b"c" * 40 + b" " + b"d" * 40 + b" M\0scripts/ci_workload.py\0"
PR_REF = "refs/pull/42/merge"
MAIN_REF = "refs/heads/main"
MANUAL_REF = "refs/heads/work/check"
RELEASE_REF = "refs/heads/release/v0.9.34"
JOBS = ("Focused CI policy tests (ubuntu-24.04)", "Quarantine registry")


@pytest.fixture
def router(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    from scripts import ci_workload

    return ci_workload


def route(router, event="pull_request", ref=PR_REF, payload=None, **overrides):
    focused = overrides.pop("focused", None)
    dispatch_pr = overrides.pop("dispatch_pr", None)
    source = {"repository": router.REPOSITORY, "base": BASE, "head": HEAD, "raw_diff": RAW_DIFF} | overrides
    if payload is None and event == "pull_request":
        payload = {
            "pull_request": {
                "number": int(ref.split("/")[2]),
                "base": {"sha": source["base"], "ref": "main", "repo": {"full_name": router.REPOSITORY}},
                "head": {"sha": source["head"]},
            },
        }
    return router.plan_workload(
        router.WorkloadSource(event=event, ref=ref, payload=payload or {}, **source),
        focused=focused,
        dispatch_pr=dispatch_pr,
    )


def focused_scope(router, event="pull_request", ref=PR_REF, base=BASE, diff=RAW_DIFF):
    return router.FocusedPlan(event, ref, base, HEAD, hashlib.sha256(diff).hexdigest(), JOBS)


def release_pr(router):
    return {
        "number": 42,
        "title": "chore(release): v0.9.34",
        "state": "open",
        "draft": False,
        "head": {"ref": "release/v0.9.34", "sha": HEAD, "repo": {"full_name": router.REPOSITORY}},
        "base": {"ref": "main", "sha": BASE, "repo": {"full_name": router.REPOSITORY}},
    }


@pytest.mark.parametrize(
    ("event", "ref"),
    [("pull_request", PR_REF), ("push", MAIN_REF), ("workflow_dispatch", MANUAL_REF), ("schedule", MAIN_REF)],
)
def test_ordinary_events_require_an_exact_focused_scope(router, event, ref):
    scope = focused_scope(router, event, ref)
    assert route(router, event, ref, focused=scope) == scope
    with pytest.raises(router.CandidateError, match="no reviewed focused workload"):
        route(router, event, ref)


@pytest.mark.parametrize("event", ["workflow_dispatch", "schedule"])
def test_periodic_and_manual_baselines_still_require_review_of_the_exact_head(router, event):
    scope = focused_scope(router, event, MAIN_REF, base=HEAD, diff=b"")
    assert route(router, event, MAIN_REF, base=HEAD, raw_diff=b"", focused=scope) == scope
    with pytest.raises(router.CandidateError, match="no reviewed focused workload"):
        route(router, event, MAIN_REF, base=HEAD, raw_diff=b"")


@pytest.mark.parametrize(
    "changed",
    [
        {"event": "workflow_dispatch"},
        {"ref": "refs/pull/43/merge"},
        {"base": "e" * 40},
        {"head": "f" * 40},
        {"raw_diff": RAW_DIFF + b"extra"},
    ],
)
def test_focused_scope_cannot_be_reused_for_another_event_or_source(router, changed):
    with pytest.raises(router.CandidateError, match="no reviewed focused workload"):
        route(router, focused=focused_scope(router), **changed)


@pytest.mark.parametrize(
    "jobs", [(), ("",), ("Focused", "Focused"), ("Coverage",), ("Test (ubuntu-24.04, Python 3.12, unit)",)]
)
def test_empty_duplicate_and_full_workloads_are_refused_as_focused_scope(router, jobs):
    with pytest.raises(router.CandidateError, match=r"reviewed focused jobs|reserved for release candidates"):
        route(router, focused=replace(focused_scope(router), jobs=jobs))


def test_change_events_cannot_adopt_a_baseline_or_missing_source(router):
    with pytest.raises(router.CandidateError, match="exact change"):
        route(router, base=HEAD, raw_diff=b"", focused=focused_scope(router, base=HEAD, diff=b""))
    with pytest.raises(router.CandidateError, match="exact source commits"):
        route(router, base="0" * 40, focused=focused_scope(router, base="0" * 40))


def test_pull_request_event_must_match_the_exact_ref_and_commits(router):
    with pytest.raises(router.CandidateError, match="missing pull request event metadata"):
        route(router, payload={}, focused=focused_scope(router))
    payload = {"pull_request": release_pr(router)}
    with pytest.raises(router.CandidateError, match="event differs from the exact ref and source"):
        route(router, ref="refs/pull/43/merge", payload=payload)
    with pytest.raises(router.CandidateError, match="event differs from the exact ref and source"):
        route(router, payload=payload, head="f" * 40)


def test_context_outside_ci_events_refs_and_repository_is_refused(router):
    with pytest.raises(router.CandidateError, match="unsupported CI context"):
        route(router, "workflow_dispatch", "release/v0.9.34", dispatch_pr=release_pr(router))
    with pytest.raises(router.CandidateError, match="unsupported CI context"):
        route(router, "workflow_dispatch", RELEASE_REF, repository="someone/fork", dispatch_pr=release_pr(router))
    with pytest.raises(router.CandidateError, match="unsupported CI context"):
        route(router, "workflow_run", MAIN_REF, focused=focused_scope(router, "workflow_run", MAIN_REF))
    with pytest.raises(router.CandidateError, match="CI event must be on main"):
        route(router, "push", MANUAL_REF, focused=focused_scope(router, "push", MANUAL_REF))


@pytest.mark.parametrize("event", ["pull_request", "workflow_dispatch"])
def test_canonical_open_release_pr_uses_current_full_candidate_contract(router, event):
    from scripts import merge_gate, release_candidate

    pr = release_pr(router)
    if event == "pull_request":
        result = route(router, payload={"pull_request": pr}, focused=focused_scope(router))
    else:
        result = route(router, event, RELEASE_REF, dispatch_pr=pr)

    assert isinstance(result, router.CandidatePlan)
    assert result.version == "0.9.34"
    assert dict(result.expected_jobs) == release_candidate.expected_jobs(event)
    assert dict(result.expected_jobs)["Coverage"] == "success"
    assert len([name for name, _ in result.expected_jobs if name.startswith("Test (")]) == 16
    assert len([name for name, _ in result.expected_jobs if name.startswith("End-to-end ")]) == 8
    assert result.coverage_floor == merge_gate.red_below((ROOT / release_candidate.WORKFLOW).read_text()) == 80


def test_release_pull_request_still_requires_its_exact_reported_base(router):
    pr = release_pr(router)
    pr["base"]["sha"] = "e" * 40
    with pytest.raises(router.CandidateError, match="canonical open release PR"):
        route(router, payload={"pull_request": pr})


@pytest.mark.parametrize("defect", ["absent", "closed", "draft", "wrong title", "fork", "wrong head", "wrong base"])
def test_release_dispatch_branch_requires_its_exact_open_canonical_pr(router, defect):
    pr = release_pr(router)
    match defect:
        case "absent":
            pr = None
        case "closed":
            pr["state"] = "closed"
        case "draft":
            pr["draft"] = True
        case "wrong title":
            pr["title"] = "fix: release"
        case "fork":
            pr["head"]["repo"]["full_name"] = "someone/fork"
        case "wrong head":
            pr["head"]["sha"] = "e" * 40
        case "wrong base":
            pr["base"]["sha"] = "e" * 40
    with pytest.raises(router.CandidateError, match="canonical open release PR"):
        route(router, "workflow_dispatch", RELEASE_REF, dispatch_pr=pr)


def test_fork_release_pr_and_release_named_tag_can_only_use_focused_scope(router):
    pr = release_pr(router)
    pr["head"]["repo"]["full_name"] = "someone/fork"
    scope = focused_scope(router)
    assert route(router, payload={"pull_request": pr}, focused=scope) == scope

    tag = "refs/tags/release/v0.9.34"
    scope = focused_scope(router, "workflow_dispatch", tag)
    assert route(router, "workflow_dispatch", tag, dispatch_pr=release_pr(router), focused=scope) == scope
