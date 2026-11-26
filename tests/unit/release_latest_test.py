"""Only a release squash with an authenticated chain receipt defers Latest."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
HEAD, MERGE, BASE, TREE = (letter * 40 for letter in "abcd")
TAG = "v0.9.56"
REPO = "helmut-hoffer-von-ankershoffen/heinzel"
TITLE = f"chore(release): {TAG}"
MESSAGE = TITLE + " (#9)\n\nHeinzel-Release-Tail: 1729\n"


@pytest.fixture
def latest(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import release_latest

    return release_latest


def lineage(*, marked=True, candidate_message=TITLE):
    def commit(sha, message):
        return {"sha": sha, "parents": [{"sha": BASE}], "commit": {"tree": {"sha": TREE}, "message": message}}

    statuses = [{"context": "heinzel/release-tail-required", "state": "success"}] if marked else []

    return {
        "actions/runs/1729/attempts/1": {
            "id": 1729,
            "run_attempt": 1,
            "path": ".github/workflows/release-chain.yml",
            "head_branch": "main",
            "event": "workflow_dispatch",
            "repository": {"full_name": REPO},
            "head_repository": {"full_name": REPO},
        },
        "pulls/9": {
            "number": 9,
            "title": TITLE,
            "state": "closed",
            "merged": True,
            "merge_commit_sha": MERGE,
            "head": {"sha": HEAD, "ref": f"release/{TAG}", "repo": {"full_name": REPO}},
            "base": {"ref": "main", "repo": {"full_name": REPO}},
        },
        f"commits/{MERGE}/pulls?per_page=100": [
            {"number": 9, "merged_at": "2026-11-26", "merge_commit_sha": MERGE, "head": {"sha": HEAD}},
        ],
        f"commits/{HEAD}/status?per_page=100&page=1": {"sha": HEAD, "total_count": len(statuses), "statuses": statuses},
        f"commits/{HEAD}": commit(HEAD, candidate_message),
        f"commits/{MERGE}": commit(MERGE, MESSAGE),
    }


def receipt():
    return {
        "schema": 1,
        "version": TAG[1:],
        "head": HEAD,
        "tree": TREE,
        "pr": 9,
        "candidate_run": 77,
        "chain_run": 1729,
    }


def test_legacy_release_uses_the_default_even_without_the_chain_workflow(latest):
    def forbidden(*_args):
        pytest.fail("legacy release cannot depend on the new chain")

    assert not latest.defer_latest(TAG, MERGE, TITLE + " (#9)\n", lineage(marked=False).get, forbidden)


def test_isolated_launcher_loads_only_the_checked_out_chain(tmp_path):
    (tmp_path / "release_chain.py").write_text('raise AssertionError("untrusted chain module")\n', encoding="utf-8")
    gh = tmp_path / "gh"
    gh.write_text("#!/bin/sh\necho '[]'\n", encoding="utf-8")
    gh.chmod(0o755)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=15).strip()
    result = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts/release_latest.py"), TAG, head],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(tmp_path), "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 1
    assert "the tag commit has no unique merged release PR" in result.stderr


def test_the_official_first_attempt_receipt_binds_the_exact_release_squash(latest):
    fetched = []
    assert latest.defer_latest(TAG, MERGE, MESSAGE, lineage().get, lambda run: fetched.append(run) or receipt())
    assert fetched == [1729]


@pytest.mark.parametrize("status_state", ["success", "failure", "pending", "error"])
def test_same_head_manual_squash_is_held_even_if_the_workflow_and_receipt_are_gone(latest, status_state):
    facts = lineage()
    facts.pop("actions/runs/1729/attempts/1")
    facts[f"commits/{HEAD}/status?per_page=100&page=1"]["statuses"][0].update(
        context="HEINZEL/Release-Tail-Required", state=status_state
    )
    with pytest.raises(latest.ChainError, match="no verified defer-Latest merge trailer"):
        latest.defer_latest(TAG, MERGE, TITLE + " (#9)", facts.get, lambda *_: pytest.fail("no artifact can unhold it"))


def test_new_chain_cut_cannot_publish_in_the_gap_before_its_status_is_written(latest):
    facts = lineage(marked=False, candidate_message=TITLE + "\n\nHeinzel-Release-Candidate: hosted-chain")
    with pytest.raises(latest.ChainError, match="no verified defer-Latest merge trailer"):
        latest.defer_latest(TAG, MERGE, TITLE + " (#9)", facts.get, lambda *_: pytest.fail("no artifact can unhold it"))


def test_trailer_cannot_stand_in_for_the_durable_status(latest):
    with pytest.raises(latest.ChainError, match="no durable status"):
        latest.defer_latest(TAG, MERGE, MESSAGE, lineage(marked=False).get, lambda *_: pytest.fail("no durable status"))


@pytest.mark.parametrize(
    "defect", ["read-error", "direct-tag", "duplicate-pr", "head-changed", "missing-status", "incomplete-status"]
)
def test_unmarked_legacy_requires_complete_exact_pr_and_status_evidence(latest, defect):
    facts = lineage(marked=False)
    associated = f"commits/{MERGE}/pulls?per_page=100"
    status = f"commits/{HEAD}/status?per_page=100&page=1"
    if defect == "read-error":
        facts.pop(associated)
    elif defect == "direct-tag":
        facts[associated] = []
    elif defect == "duplicate-pr":
        facts[associated].append(facts[associated][0])
    elif defect == "head-changed":
        facts["pulls/9"]["head"]["sha"] = MERGE
    elif defect == "missing-status":
        facts.pop(status)
    else:
        facts[status]["total_count"] = 1
    with pytest.raises(latest.ChainError):
        latest.defer_latest(
            TAG, MERGE, TITLE, facts.get, lambda *_: pytest.fail("unmarked legacy must rule out a hold")
        )


@pytest.mark.parametrize("defect", [None, "expired", "duplicate", "different-run"])
def test_receipt_download_requires_the_unique_artifact_of_the_exact_run(latest, monkeypatch, defect):
    name = "release-defer-latest-1"
    artifact = {
        "name": name,
        "expired": defect == "expired",
        "workflow_run": {"id": 4 if defect == "different-run" else 1729},
    }
    artifacts = [artifact, artifact] if defect == "duplicate" else [artifact]
    monkeypatch.setattr(latest, "get", lambda _path: {"total_count": len(artifacts), "artifacts": artifacts})

    def download(args):
        assert not defect
        assert args[:8] == ["gh", "run", "download", "1729", "-R", REPO, "-n", name]
        (Path(args[-1]) / "release-defer-latest.json").write_text(json.dumps(receipt()), encoding="utf-8")

    monkeypatch.setattr(latest, "run", download)
    if defect:
        with pytest.raises(latest.ChainError, match="same-run defer-Latest artifact"):
            latest.receipt_for(1729)
    else:
        assert latest.receipt_for(1729) == receipt()


@pytest.mark.parametrize(
    "defect",
    [
        "unknown-run",
        "rerun",
        "wrong-workflow",
        "fork",
        "pull-request-run",
        "stale-receipt",
        "wrong-release",
        "wrong-tip",
        "changed-tree",
        "moved-parent",
        "wrong-branch",
        "duplicate",
        "malformed",
        "not-a-trailer",
    ],
)
def test_forged_or_unbound_deferral_cannot_fall_back_to_a_legacy_release(latest, defect):
    data, proof, tag, sha, message = lineage(), receipt(), TAG, MERGE, MESSAGE
    workflow = data["actions/runs/1729/attempts/1"]
    pr = data["pulls/9"]
    edits = {
        "unknown-run": lambda: data.pop("actions/runs/1729/attempts/1"),
        "rerun": lambda: workflow.update(run_attempt=2),
        "wrong-workflow": lambda: workflow.update(path=".github/workflows/release-tail.yml"),
        "fork": lambda: workflow.update(head_repository={"full_name": "other/heinzel"}),
        "pull-request-run": lambda: workflow.update(event="pull_request"),
        "stale-receipt": lambda: proof.update(chain_run=42),
        "wrong-release": lambda: proof.update(version="0.9.55"),
        "wrong-tip": lambda: pr.update(merge_commit_sha="e" * 40),
        "changed-tree": lambda: data[f"commits/{MERGE}"]["commit"].update(tree={"sha": "e" * 40}),
        "moved-parent": lambda: data[f"commits/{MERGE}"].update(parents=[{"sha": "e" * 40}]),
        "wrong-branch": lambda: pr["head"].update(ref="release/v0.9.55"),
    }
    if defect in edits:
        edits[defect]()
    elif defect == "duplicate":
        message += "Heinzel-Release-Tail: 1729\n"
    elif defect == "malformed":
        message = message.replace("1729", "fake")
    else:
        message += "replayed in prose\n"
    with pytest.raises(latest.ChainError):
        latest.defer_latest(tag, sha, message, data.get, lambda _run: proof)


def workflow_step(workflow, job, name):
    data = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8"))
    return next(step for step in data["jobs"][job]["steps"] if step.get("name") == name)["run"]


@pytest.mark.parametrize(
    ("mode", "repository", "deferred"),
    [("true", REPO, True), ("false", REPO, False), ("", REPO, None), ("", "other/plugin", False)],
)
def test_actual_publish_shell_uses_the_verified_mode_only(tmp_path, mode, repository, deferred):
    script = workflow_step("publish.yml", "publish", "Create the GitHub release")
    gh = tmp_path / "gh"
    gh.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$TRACE"\n', encoding="utf-8")
    gh.chmod(0o755)
    trace, bundle = tmp_path / "trace", tmp_path / "bundle"
    bundle.write_text('{"dsseEnvelope": {}}', encoding="utf-8")
    (tmp_path / "dist").mkdir()
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "GITHUB_REPOSITORY": repository,
        "DEFER_LATEST": mode,
        "TAG": TAG,
        "TRACE": str(trace),
        "BUNDLE": str(bundle),
    }
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script], cwd=tmp_path, env=env, check=False, timeout=15
    )
    if deferred is None:
        assert result.returncode
        assert not trace.exists()
    else:
        assert result.returncode == 0
        args = trace.read_text(encoding="utf-8").splitlines()
        assert args[:3] == ["release", "create", TAG]
        assert "--verify-tag" in args
        assert ("--latest=false" in args) is deferred


@pytest.mark.parametrize(
    ("policy", "remote", "published", "expect_tag"),
    [
        ("false", MERGE, False, False),
        ("true", MERGE, False, True),
        ("true", MERGE, True, False),
        ("true", HEAD, False, None),
    ],
)
def test_release_tag_retry_never_retags_or_repairs_completed_lineage(tmp_path, policy, remote, published, expect_tag):
    script = workflow_step("release-tag.yml", "tag", "Create and push the tag")
    commands = {
        "git": 'case "$*" in "rev-parse HEAD") echo "$GITHUB_SHA" ;; '
        '"rev-parse --verify FETCH_HEAD^{commit}") echo "$REMOTE" ;; '
        'ls-remote*|fetch*) : ;; *) echo "unexpected git $*" >&2; exit 1 ;; esac\n',
        "uv": 'if [ "$1" = version ]; then echo 0.9.56; else echo "defer_latest=$POLICY"; fi\n',
        "gh": 'if [ "$PUBLISHED" = true ]; then echo artifact-manifest.json; else exit 1; fi\n',
    }
    for name, command in commands.items():
        path = tmp_path / name
        path.write_text("#!/bin/sh\n" + command, encoding="utf-8")
        path.chmod(0o755)
    output = tmp_path / "output"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": MERGE,
        "REQUESTED_SHA": MERGE,
        "MESSAGE": MESSAGE,
        "GITHUB_OUTPUT": str(output),
        "POLICY": policy,
        "REMOTE": remote,
        "PUBLISHED": str(published).lower(),
    }
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script], cwd=tmp_path, env=env, check=False, timeout=15
    )
    assert (result.returncode == 0) is (expect_tag is not None)
    assert output.exists() is (expect_tag is True)
    if expect_tag:
        assert output.read_text(encoding="utf-8") == f"tag={TAG}\n"
