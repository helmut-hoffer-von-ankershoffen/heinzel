import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SPEC = importlib.util.spec_from_file_location(
    "gate_review_history", Path(__file__).resolve().parents[2] / "scripts" / "review_history.py"
)
history = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(history)

HEAD = "a" * 40
REPO = "owner/repo"


def workflow(run, attempt=1, **changes):
    return {
        "id": run,
        "run_attempt": attempt,
        "head_sha": HEAD,
        "event": "pull_request_target",
        "path": history.WORKFLOW,
        **changes,
    }


def gate_job(run, attempt, step=history.REJECT_STEP, outcome="skipped", **changes):
    return {
        "name": "Gate review",
        "run_id": run,
        "run_attempt": attempt,
        "head_sha": HEAD,
        "status": "completed",
        "conclusion": "failure",
        "steps": [{"name": step, "status": "completed", "conclusion": outcome}],
        **changes,
    }


def github(runs, attempts=(), *, total=None):
    def read(endpoint):
        if "/workflows/" in endpoint:
            assert f"head_sha={HEAD}" in endpoint
            assert "event=pull_request_target" in endpoint
            return {"total_count": len(runs) if total is None else total, "workflow_runs": runs}
        parts = endpoint.split("/")
        key = int(parts[-4]), int(parts[-2])
        jobs = dict(attempts)[key]
        return {"total_count": len(jobs), "jobs": jobs}

    return read


def test_an_exact_first_attempt_without_prior_rejection_remains_eligible():
    history.check(github([workflow(100)]), REPO, HEAD, 100, 1)


@pytest.mark.parametrize("separate_run", [False, True])
def test_prior_same_head_rejection_blocks_spend_limit_on_rerun_or_new_run(separate_run):
    runs = [workflow(99), workflow(100)] if separate_run else [workflow(100, attempt=2)]
    prior = 99 if separate_run else 100
    rejected = gate_job(prior, 1, outcome="success")
    with pytest.raises(ValueError, match="reviewer rejected this exact head"):
        history.check(github(runs, [((prior, 1), [rejected])]), REPO, HEAD, 100, 1 if separate_run else 2)


def test_standing_exception_still_works_after_legacy_approval_or_modern_nonrejection():
    runs = [workflow(98), workflow(99), workflow(100, 2)]
    jobs = [
        ((98, 1), [gate_job(98, 1, history.LEGACY_JUDGE, "success", conclusion="success")]),
        ((99, 1), [gate_job(99, 1)]),
        ((100, 1), [gate_job(100, 1)]),
    ]
    history.check(github(runs, jobs), REPO, HEAD, 100, 2)


@pytest.mark.parametrize(
    "jobs",
    [
        [],
        [gate_job(99, 1, history.LEGACY_JUDGE, "failure")],
        [gate_job(99, 1, status="in_progress")],
        [gate_job(99, 1, outcome="cancelled")],
        [gate_job(99, 1, conclusion="cancelled")],
        [gate_job(99, 1, head_sha="b" * 40)],
        [gate_job(99, 2)],
    ],
)
def test_unverified_or_ambiguous_prior_attempts_block_skip(jobs):
    with pytest.raises(ValueError, match="prior same-head Gate"):
        history.check(github([workflow(99), workflow(100)], [((99, 1), jobs)]), REPO, HEAD, 100, 1)


@pytest.mark.parametrize(
    ("runs", "total"),
    [
        ([workflow(100)], 2),
        ([], 0),
        ([workflow(100, 2)], 1),
        ([workflow(100, head_sha="b" * 40)], 1),
        ([workflow(100), workflow(100)], 2),
    ],
)
def test_incomplete_run_inventory_and_mismatched_current_head_block_skip(runs, total):
    with pytest.raises(ValueError, match=r"inventory|identity"):
        history.check(github(runs, total=total), REPO, HEAD, 100, 1)
