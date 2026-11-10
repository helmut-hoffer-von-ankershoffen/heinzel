import hashlib
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import pytest

p = Path(__file__).resolve().parents[2] / "scripts" / "one_shot_focused_reader_recovery.py"
loader = importlib.machinery.SourceFileLoader("reader_recovery_draft", str(p))
spec = importlib.util.spec_from_loader(loader.name, loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)
SHA = "a" * 40
ENV = {
    "RECOVERY_SHA": SHA,
    "GITHUB_SHA": SHA,
    "GITHUB_WORKFLOW_SHA": SHA,
    "GITHUB_ACTIONS": "true",
    "RUNNER_ENVIRONMENT": "github-hosted",
    "GITHUB_REPOSITORY": m.REPO,
    "GITHUB_EVENT_NAME": "push",
    "GITHUB_REF": m.REF,
    "GITHUB_WORKFLOW_REF": f"{m.REPO}/{m.WORKFLOW}@{m.REF}",
    "GITHUB_ACTOR": m.REPO.partition("/")[0],
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_RUN_ID": "99",
    "RECOVERY_WRITER_HOLD": m.CURRENT_MAIN,
    "RECOVERY_TARGET": "695",
    "FOCUSED_CI_PLANS": "{}",
}


pytestmark = pytest.mark.unit


class RecoveryTests(unittest.TestCase):
    def test_manifest_digest_exact(self):
        head = m.TARGETS[0][1]
        plan = {"schema": 1}
        raw = json.dumps({"pull_request:" + head: plan})
        digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with mock.patch.dict(m.PLAN_DIGESTS, {head: digest}):
            m.check_plan(head, raw)
            with pytest.raises(ValueError, match="changed"):
                m.check_plan(head, json.dumps({"pull_request:" + head: {"schema": 2}}))

    def test_wrong_workflow_ref_never_posts(self):
        with (
            mock.patch.dict(os.environ, {**ENV, "GITHUB_REF": "refs/heads/wrong"}, clear=True),
            mock.patch.object(m, "publish") as post,
        ):
            with pytest.raises(ValueError, match="unapproved"):
                m.main(Path(), Path())
            post.assert_not_called()

    def test_failed_judge_invalidates_both_contexts(self):
        calls = []

        def execute(args, **kw):
            if "sonar" in args:
                raise subprocess.CalledProcessError(1, args)
            return "genuine pass"

        with (
            mock.patch.dict(os.environ, ENV, clear=True),
            mock.patch.object(m, "TARGETS", m.TARGETS[:1]),
            mock.patch.object(m, "root_commit"),
            mock.patch.object(m, "only_recovery_run"),
            mock.patch.object(m, "check_plan"),
            mock.patch.object(m, "current"),
            mock.patch.object(m, "run", side_effect=execute),
            mock.patch.object(m, "publish", side_effect=lambda head, ctx, state, url: calls.append((ctx, state))),
            pytest.raises(subprocess.CalledProcessError),
        ):
            m.main(Path(), Path())
        assert not calls

    def test_source_change_invalidates_existing_pending(self):
        calls = []
        counter = 0

        def current(*_):
            nonlocal counter
            counter += 1
            if counter == 4:
                msg = "main moved"
                raise ValueError(msg)

        with (
            mock.patch.dict(os.environ, ENV, clear=True),
            mock.patch.object(m, "TARGETS", m.TARGETS[:1]),
            mock.patch.object(m, "root_commit"),
            mock.patch.object(m, "only_recovery_run"),
            mock.patch.object(m, "check_plan"),
            mock.patch.object(m, "current", side_effect=current),
            mock.patch.object(m, "run", return_value="pass"),
            mock.patch.object(
                m, "publish", side_effect=lambda head, ctx, state, url: (calls.append((ctx, state)), 789)[1]
            ),
            pytest.raises(ValueError, match="main moved"),
        ):
            m.main(Path(), Path())
        assert calls == [(None, "pending"), (789, "failure")]


if __name__ == "__main__":
    unittest.main()


def test_second_recovery_run_fails_closed(monkeypatch):
    monkeypatch.setenv("RECOVERY_SHA", SHA)
    monkeypatch.setenv("GITHUB_RUN_ID", "99")
    monkeypatch.setattr(
        m,
        "api",
        lambda _: {"total_count": 2, "workflow_runs": [{"id": 99, "run_attempt": 1}, {"id": 100, "run_attempt": 1}]},
    )
    with pytest.raises(ValueError, match="not unique"):
        m.only_recovery_run()


def test_check_readback_requires_same_id(monkeypatch):
    head = m.TARGETS[0][1]
    response = {
        "id": 1,
        "name": "coverage-floor",
        "head_sha": head,
        "status": "in_progress",
        "conclusion": None,
        "details_url": "test",
        "app": {"id": m.ACTIONS_APP_ID},
        "check_suite": {"id": m.TARGETS[0][6]},
    }
    monkeypatch.setattr(m, "run", lambda *_: json.dumps(response))
    monkeypatch.setattr(
        m,
        "api",
        lambda path: (
            {"id": m.TARGETS[0][6], "head_sha": head, "app": {"id": m.ACTIONS_APP_ID}}
            if path.startswith("check-suites/")
            else {**response, "id": 2}
        ),
    )
    with pytest.raises(ValueError, match="readback differs"):
        m.publish(m.TARGETS[0], None, "pending", "test")


@pytest.mark.parametrize("target_index", [0, 1])
@pytest.mark.parametrize(
    "defect", [None, "base", "head", "parents", "run", "rules", "protected_suite", "already_green"]
)
def test_current_rejects_identity_or_policy_drift(monkeypatch, defect, target_index):
    target = m.TARGETS[target_index]
    number, head, branch, merge, tree, run_id, suite, protected_suite, failed, green = target
    repo = {"full_name": m.REPO}
    rows = [{"context": name, "integration_id": app} for name, app in m.CONTEXTS.items()]
    data = {
        f"pulls/{number}": {
            "number": number,
            "state": "open",
            "draft": False,
            "head": {"sha": head, "ref": branch, "repo": repo},
            "base": {"sha": m.BASE, "ref": "main", "repo": repo},
            "merge_commit_sha": merge,
        },
        "git/ref/heads/main": {"object": {"sha": m.CURRENT_MAIN}},
        f"git/ref/heads/{branch}": {"object": {"sha": head}},
        f"git/commits/{merge}": {"sha": merge, "tree": {"sha": tree}, "parents": [{"sha": m.BASE}, {"sha": head}]},
        f"actions/runs/{run_id}": {
            "id": run_id,
            "run_attempt": 1,
            "check_suite_id": suite,
            "head_sha": head,
            "head_branch": branch,
            "path": ".github/workflows/ci.yml",
            "workflow_id": m.CI_WORKFLOW_ID,
            "event": "pull_request",
            "status": "completed",
            "conclusion": "success",
            "repository": repo,
            "head_repository": repo,
            "display_title": f"CI {merge}",
        },
        f"actions/workflows/{m.CI_WORKFLOW_ID}/runs?head_sha={head}&event=pull_request&per_page=1": {
            "workflow_runs": [{"id": run_id}]
        },
        f"commits/{head}/check-runs?check_name=coverage-floor&filter=latest&per_page=100": {
            "total_count": 1,
            "check_runs": [
                {"id": failed, "name": "coverage-floor", "app": {"id": m.ACTIONS_APP_ID}, "conclusion": "failure"}
            ],
        },
        "rules/branches/main": [
            {
                "type": "required_status_checks",
                "ruleset_id": m.RULESET,
                "ruleset_source": m.REPO,
                "parameters": {"required_status_checks": rows, "strict_required_status_checks_policy": False},
            }
        ],
    }
    for check_id, name, outcome in (
        (failed, "coverage-floor", "failure"),
        (green, "merge-gate", "success"),
    ):
        data[f"check-runs/{check_id}"] = {
            "id": check_id,
            "name": name,
            "head_sha": head,
            "app": {"id": m.ACTIONS_APP_ID},
            "check_suite": {"id": protected_suite},
            "status": "completed",
            "conclusion": outcome,
        }
    if defect == "base":
        data["git/ref/heads/main"]["object"]["sha"] = "b" * 40
    elif defect == "head":
        data[f"git/ref/heads/{branch}"]["object"]["sha"] = "b" * 40
    elif defect == "parents":
        data[f"git/commits/{merge}"]["parents"].reverse()
    elif defect == "run":
        data[f"actions/runs/{run_id}"]["conclusion"] = "failure"
    elif defect == "rules":
        data["rules/branches/main"][0]["parameters"]["required_status_checks"].pop()
    elif defect == "protected_suite":
        data[f"check-runs/{failed}"]["check_suite"]["id"] = suite if suite != protected_suite else suite + 1
    elif defect == "already_green":
        data[f"commits/{head}/check-runs?check_name=coverage-floor&filter=latest&per_page=100"]["check_runs"][0][
            "conclusion"
        ] = "success"
    monkeypatch.setattr(m, "only_recovery_run", lambda: None)
    monkeypatch.setattr(m, "api", lambda path: data[path])
    if defect is None:
        m.current(target)
    else:
        with pytest.raises(ValueError, match=r"moved|changed"):
            m.current(target)


def test_only_explicit_reviewed_target_is_selected(monkeypatch):
    monkeypatch.setenv("RECOVERY_TARGET", "696")
    assert m.selected_target() == m.TARGETS[1]
    monkeypatch.setenv("RECOVERY_TARGET", "699")
    with pytest.raises(ValueError, match="unapproved"):
        m.selected_target()


def test_original_and_protected_suites_are_distinct_for_696():
    target = m.TARGETS[1]
    assert target[6] == 115953479871
    assert target[7] == 115953479517


def test_cleanup_only_invalidates_owned_check(monkeypatch):
    head = m.TARGETS[0][1]
    target_url = "https://github.com/owner/repo/actions/runs/99"
    row = {
        "id": 900,
        "name": "coverage-floor",
        "head_sha": head,
        "app": {"id": m.ACTIONS_APP_ID},
        "check_suite": {"id": m.TARGETS[0][6]},
        "details_url": target_url,
    }
    monkeypatch.setenv("RECOVERY_TARGET", "695")
    monkeypatch.setattr(m, "host", lambda: target_url)
    monkeypatch.setattr(m, "api", lambda _: {"total_count": 1, "check_runs": [row]})
    post = mock.Mock()
    monkeypatch.setattr(m, "publish", post)
    m.cleanup()
    post.assert_called_once_with(m.TARGETS[0], 900, "failure", target_url)
    row["id"] = m.TARGETS[0][8]
    post.reset_mock()
    with pytest.raises(ValueError, match="ownership"):
        m.cleanup()
    post.assert_not_called()
