"""A partial or forged batch must never suppress a full main suite."""

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "main_batch", Path(__file__).resolve().parents[2] / "scripts/main_batch.py"
)
assert SPEC
assert SPEC.loader
batch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(batch)
pytestmark = pytest.mark.unit
BASE, FIRST, FINAL = "a" * 40, "b" * 40, "c" * 40


@pytest.fixture
def evidence():
    proof = {"v": 1, "base": BASE, "prs": [[12, FIRST], [13, FINAL]], "run": 99}
    status = {
        "context": batch.CONTEXT,
        "state": "success",
        "creator": {"id": batch.PUBLISHER_ID},
        "target_url": batch.proof_url(BASE, proof["prs"], 99),
    }
    rows = {
        f"commits/{FIRST}/statuses?per_page=100": [deepcopy(status)],
        f"commits/{FINAL}/statuses?per_page=100": [deepcopy(status)],
        "actions/runs/99": {
            "id": 99,
            "run_attempt": 1,
            "event": "push",
            "head_branch": "main",
            "head_sha": FINAL,
            "path": ".github/workflows/ci.yml",
            "repository": {"full_name": batch.REPOSITORY},
            "status": "in_progress",
        },
        f"compare/{BASE}...{FINAL}": {
            "status": "ahead",
            "total_commits": 2,
            "commits": [{"sha": FIRST, "parents": [{"sha": BASE}]}, {"sha": FINAL, "parents": [{"sha": FIRST}]}],
        },
        "actions/runs/99/attempts/1/jobs?per_page=100": {
            "total_count": 1,
            "jobs": [{"name": f"Main batch full suite ({BASE})", "status": "completed", "conclusion": "success"}],
        },
    }
    for number, sha in proof["prs"]:
        rows[f"pulls/{number}"] = {
            "merged": True,
            "merge_commit_sha": sha,
            "base": {"ref": "main", "repo": {"full_name": batch.REPOSITORY}},
        }
    return rows


def test_intermediate_delegates_only_after_final_consumes_aggregate_base(evidence):
    assert batch.admit(FIRST, evidence.__getitem__, seconds=0) == {"full": False, "base": BASE, "run": 99}


def test_final_always_runs_full_and_can_start_before_its_marker(evidence):
    del evidence["actions/runs/99/attempts/1/jobs?per_page=100"]
    assert batch.admit(FINAL, evidence.__getitem__, seconds=0) == {"full": True, "base": BASE, "run": 99}


@pytest.mark.parametrize(
    ("endpoint", "path", "value"),
    [
        (f"commits/{FIRST}/statuses?per_page=100", (0, "creator", "id"), 7),
        (f"commits/{FIRST}/statuses?per_page=100", (0, "context"), "CI OK"),
        (f"commits/{FINAL}/statuses?per_page=100", (), []),
        ("actions/runs/99", ("run_attempt",), 2),
        ("actions/runs/99", ("path",), ".github/workflows/other.yml"),
        ("actions/runs/99", ("head_sha",), FIRST),
        ("actions/runs/99", ("head_branch",), "topic"),
        ("actions/runs/99", ("repository", "full_name"), "other/repository"),
        ("actions/runs/99", ("status",), "cancelled"),
        (f"compare/{BASE}...{FINAL}", ("total_commits",), 3),
        (f"compare/{BASE}...{FINAL}", ("commits", 1, "parents"), [{"sha": BASE}]),
        ("pulls/12", ("merged",), False),
        ("pulls/12", ("merge_commit_sha",), FINAL),
        ("actions/runs/99/attempts/1/jobs?per_page=100", ("jobs", 0, "conclusion"), "skipped"),
        ("actions/runs/99/attempts/1/jobs?per_page=100", ("total_count",), 101),
        (f"commits/{FIRST}/statuses?per_page=100", (0, "target_url"), "https://example.invalid/?batch=bad"),
    ],
)
def test_bad_evidence_runs_full(evidence, endpoint, path, value):
    if not path:
        evidence[endpoint] = value
        assert batch.admit(FIRST, evidence.__getitem__, seconds=0)["full"] is True
        return
    parent = evidence[endpoint]
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    assert batch.admit(FIRST, evidence.__getitem__, seconds=0) == {"full": True, "base": "", "run": ""}


def test_missing_proof_wait_is_bounded_without_real_sleep():
    ticks = [0]
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        ticks[0] += seconds

    result = batch.admit(FIRST, lambda _: {}, seconds=12, clock=lambda: ticks[0], sleep=sleep)
    assert result["full"] is True
    assert waits == [5, 5, 2]


@pytest.mark.parametrize(
    "members",
    [
        [],
        [[12, FIRST]],
        [[12, FIRST], [12, FINAL]],
        [[12, FIRST], [13, FIRST]],
        [[True, FIRST], [13, FINAL]],
        [[n, f"{n:040x}"] for n in range(1, 7)],
    ],
)
def test_invalid_members_never_decode(members):
    assert batch.decode(batch.proof_url(BASE, members, 99)) is None


def test_newer_untrusted_status_revokes_older_trusted_proof(evidence):
    statuses = evidence[f"commits/{FIRST}/statuses?per_page=100"]
    statuses.insert(0, {**statuses[0], "creator": {"id": 7}})
    assert batch.admit(FIRST, evidence.__getitem__, seconds=0)["full"] is True


def test_newer_case_variant_is_not_ignored(evidence):
    statuses = evidence[f"commits/{FIRST}/statuses?per_page=100"]
    statuses.insert(0, {**statuses[0], "context": batch.CONTEXT.upper(), "state": "failure"})
    assert batch.admit(FIRST, evidence.__getitem__, seconds=0)["full"] is True


def test_cancelled_final_run_does_not_delegate(evidence):
    evidence["actions/runs/99"].update(status="completed", conclusion="cancelled")
    assert batch.admit(FIRST, evidence.__getitem__, seconds=0)["full"] is True


@pytest.mark.parametrize(
    "malformed",
    [
        None,
        {},
        {"name": "", "status": "queued", "conclusion": None},
        {"name": "Other job", "status": "unknown", "conclusion": None},
        {"name": "Other job", "status": "completed", "conclusion": None},
        {"name": "Other job", "status": "completed", "conclusion": "unknown"},
        {"name": "Other job", "status": "in_progress", "conclusion": "success"},
    ],
)
def test_malformed_unrelated_job_before_valid_marker_runs_full(evidence, malformed):
    page = evidence["actions/runs/99/attempts/1/jobs?per_page=100"]
    page["jobs"].insert(0, malformed)
    page["total_count"] += 1
    assert batch.admit(FIRST, evidence.__getitem__, seconds=0)["full"] is True


@pytest.mark.parametrize("status", ["queued", "waiting", "in_progress", "pending", "requested"])
def test_legitimate_pending_job_before_valid_marker_still_delegates(evidence, status):
    page = evidence["actions/runs/99/attempts/1/jobs?per_page=100"]
    page["jobs"].insert(0, {"name": "Test (macos-latest)", "status": status, "conclusion": None})
    page["total_count"] += 1
    assert batch.admit(FIRST, evidence.__getitem__, seconds=0)["full"] is False
