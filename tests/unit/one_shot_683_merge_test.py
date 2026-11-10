"""Offline evidence and safety checks for the one-shot merge."""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
spec = importlib.util.spec_from_file_location("merge683", Path(__file__).parents[2] / "scripts/one_shot_683_merge.py")
assert spec
assert spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_requires_separately_reviewed_helper_before_api(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", mod.REPO)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/bootstrap/one-shot-683-merge")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("REVIEWED_HELPER_SHA", "b" * 40)
    monkeypatch.setattr(mod, "one", lambda _: pytest.fail("unexpected API call"))
    with pytest.raises(ValueError, match="helper SHA"):
        mod.validate()


def test_merge_uses_head_bound_normal_squash(monkeypatch):
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command

        class Result:
            stdout = '{"merged": true}'

        return Result()

    monkeypatch.setattr(mod.subprocess, "run", run)
    mod.one_merge()
    assert f"sha={mod.HEAD}" in captured["command"]
    assert "merge_method=squash" in captured["command"]
    assert not any("admin" in part for part in captured["command"])


def test_validate_reads_test_merge_ref_without_pr_merge_sha(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", mod.REPO)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/bootstrap/one-shot-683-merge")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("REVIEWED_HELPER_SHA", "a" * 40)
    objects = {
        "pulls/683": {
            "state": "open",
            "draft": False,
            "head": {"sha": mod.HEAD},
            "base": {"sha": mod.BASE, "ref": "main"},
            "mergeable": True,
            "mergeable_state": "clean",
        },
        "commits/main": {"sha": mod.BASE},
        f"git/commits/{mod.HEAD}": {"tree": {"sha": mod.TREE}},
        "git/ref/pull/683/merge": {"object": {"sha": "merge-oid"}},
        "git/commits/merge-oid": {"tree": {"sha": mod.TREE}, "parents": [{"sha": mod.BASE}, {"sha": mod.HEAD}]},
    }
    monkeypatch.setattr(mod, "one", lambda path: objects[path])
    validated = []
    monkeypatch.setattr(mod, "validate_evidence", lambda: validated.append(mod.RECOVERY_RUN_ID))
    monkeypatch.setattr(mod, "validate_unstable", lambda: validated.append("unstable"))
    mod.validate()
    assert validated == [42801046287, "unstable"]
    objects["pulls/683"]["mergeable_state"] = "unstable"
    mod.validate()
    assert validated[-1] == "unstable"
    objects["pulls/683"]["mergeable_state"] = "blocked"
    with pytest.raises(ValueError, match="pull request"):
        mod.validate()
    objects["pulls/683"]["mergeable_state"] = "clean"
    objects["git/commits/merge-oid"]["parents"].reverse()
    with pytest.raises(ValueError, match="test merge changed"):
        mod.validate()


@pytest.fixture
def unstable(monkeypatch):
    suites = [
        {
            "id": suite,
            "head_sha": mod.HEAD,
            "status": "completed",
            "conclusion": "action_required",
            "app": {"slug": "github-actions"},
            "latest_check_runs_count": 0,
            "pull_requests": [{"number": 683, "head": {"sha": mod.HEAD}, "base": {"sha": mod.PR_SNAPSHOT_BASE}}],
        }
        for suite in mod.UNSTABLE_SUITES
    ]
    runs = {
        run: {
            "id": run,
            "check_suite_id": suite,
            "head_sha": mod.HEAD,
            "event": "pull_request",
            "path": path,
            "run_attempt": 1,
            "status": "completed",
            "conclusion": "action_required",
            "repository": {"id": mod.REPOSITORY_ID},
            "head_repository": {"id": mod.REPOSITORY_ID},
            "actor": {"id": mod.ACTIONS_BOT_ID},
            "triggering_actor": {"id": mod.ACTIONS_BOT_ID},
            "pull_requests": [{"number": 683, "head": {"sha": mod.HEAD}, "base": {"sha": mod.PR_SNAPSHOT_BASE}}],
        }
        for suite, (run, path) in mod.UNSTABLE_SUITES.items()
    }
    jobs = {run: [] for run in runs}

    def api(path):
        if path.startswith("commits/"):
            return [{"total_count": len(suites), "check_suites": suites}]
        for run, run_value in runs.items():
            if path == f"actions/runs/{run}":
                return [run_value]
            if path == f"actions/runs/{run}/attempts/1/jobs?per_page=100":
                return [{"total_count": len(jobs[run]), "jobs": jobs[run]}]
        pytest.fail(f"unexpected API path {path}")

    monkeypatch.setattr(mod, "api", api)
    return suites, runs, jobs


def test_accepts_exact_zero_job_unstable_suites(unstable):
    mod.validate_unstable()


@pytest.mark.parametrize("mutation", ["extra_suite", "suite_verdict", "run_head", "run_attempt", "job_acquired"])
def test_refuses_changed_unstable_suites(unstable, mutation):
    suites, runs, jobs = unstable
    run = next(iter(runs))
    if mutation == "extra_suite":
        suites.append(
            {
                "id": 999,
                "pull_requests": [{"number": 683, "head": {"sha": mod.HEAD}, "base": {"sha": mod.PR_SNAPSHOT_BASE}}],
                "status": "queued",
            }
        )
    elif mutation == "suite_verdict":
        suites[0]["conclusion"] = "failure"
    elif mutation == "run_head":
        runs[run]["head_sha"] = "0" * 40
    elif mutation == "run_attempt":
        runs[run]["run_attempt"] = 2
    else:
        jobs[run].append({"id": 1})
    with pytest.raises(ValueError, match=r"suite|run"):
        mod.validate_unstable()
