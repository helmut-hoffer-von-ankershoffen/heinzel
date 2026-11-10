"""A partial or forged batch must never suppress a full main suite."""

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

SPEC = importlib.util.spec_from_file_location(
    "main_batch", Path(__file__).resolve().parents[2] / "scripts/main_batch.py"
)
assert SPEC
assert SPEC.loader
batch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(batch)
pytestmark = pytest.mark.unit
BASE, FIRST, FINAL = "a" * 40, "b" * 40, "c" * 40


def test_release_delegation_uses_verified_candidate_and_fails_closed(monkeypatch):
    head, tree = "d" * 40, "e" * 40
    rows = {
        f"commits/{FINAL}": {
            "sha": FINAL,
            "commit": {"message": "chore(release): v0.9.35 (#693)", "tree": {"sha": tree}},
        },
        f"commits/{FINAL}/pulls?per_page=100": [{"number": 693, "merge_commit_sha": FINAL}],
        "pulls/693": {"title": "chore(release): v0.9.35", "head": {"sha": head}},
        f"commits/{head}/check-runs?check_name=coverage-floor&per_page=100": {
            "total_count": 1,
            "check_runs": [
                {
                    "name": "coverage-floor",
                    "head_sha": head,
                    "status": "completed",
                    "conclusion": "success",
                    "app": {"id": 15368},
                }
            ],
        },
    }
    seen = []
    monkeypatch.setattr(batch.release_candidate, "discover", lambda get, h: 99)

    def verify(_get, _download, expected, pins):
        seen.append((expected, pins))
        return {"base": BASE}

    monkeypatch.setattr(batch.release_candidate, "verify", verify)
    result = batch.release_proof_for(FINAL, rows.__getitem__, lambda _: b"", batch.RELEASE_PINS)
    assert result == {"run": 99, "base": BASE, "head": head, "tree": tree, "pr": 693}
    assert seen[0][0].merged_sha == FINAL
    rows[f"commits/{head}/check-runs?check_name=coverage-floor&per_page=100"]["check_runs"][0]["conclusion"] = "failure"
    with pytest.raises(batch.release_candidate.CandidateError):
        batch.release_proof_for(FINAL, rows.__getitem__, lambda _: b"", batch.RELEASE_PINS)


@pytest.mark.parametrize("release", [False, True])
def test_main_api_failure_falls_back_only_for_ordinary_push(monkeypatch, tmp_path, release):
    event = tmp_path / "event.json"
    output = tmp_path / "output"
    output.touch()
    message = "chore(release): v0.9.35 (#693)" if release else "fix(web): ordinary change"
    event.write_text(json.dumps({"head_commit": {"id": FINAL, "message": message}}))
    for key, value in {
        "GITHUB_REPOSITORY": batch.REPOSITORY,
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": FINAL,
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_OUTPUT": str(output),
    }.items():
        monkeypatch.setenv(key, value)

    def unavailable(*_args):
        msg = "API unavailable"
        raise OSError(msg)

    monkeypatch.setattr(batch, "release_proof_approved", unavailable)
    if release:
        with pytest.raises(OSError, match="API unavailable"):
            batch.main()
        assert output.read_text() == ""
    else:
        batch.main()
        assert "full=true" in output.read_text()


def test_release_proof_tries_only_the_two_reviewed_source_pairs(monkeypatch):
    tried = []

    def proof(_sha, _get, _download, pins):
        tried.append(pins)
        if pins == batch.RELEASE_PINS:
            msg = "not the historical sources"
            raise batch.release_candidate.CandidateError(msg)
        return {"run": 99}

    monkeypatch.setattr(batch, "release_proof_for", proof)
    assert batch.release_proof_approved(FINAL, lambda _: {}, lambda _: b"") == {"run": 99}
    assert tried == [batch.RELEASE_PINS, batch.DELEGATION_PINS]


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
    assert waits == [5]


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


@pytest.fixture
def fake_clock(monkeypatch):
    state = {"elapsed": 0, "waits": []}

    def sleep(seconds):
        state["waits"].append(seconds)
        state["elapsed"] += seconds

    monkeypatch.setattr(batch.time, "time", lambda: state["elapsed"])
    return state, lambda: state["elapsed"], sleep


def _pending_intent():
    return {
        "context": batch.CONTEXT,
        "state": "pending",
        "creator": {"id": batch.PUBLISHER_ID},
        "target_url": batch.intent_url(BASE, FIRST),
        "created_at": "1970-01-01T00:00:00Z",
    }


def test_ordinary_push_without_batch_intent_is_fast(fake_clock):
    state, clock, sleep = fake_clock
    result = batch.admit(FIRST, lambda _: [], clock=clock, sleep=sleep)
    assert result == {"full": True, "base": "", "run": ""}
    assert state["elapsed"] <= 5
    assert state["waits"] == [5]


def test_trusted_multi_pr_intent_can_wait_for_the_final_proof(evidence, fake_clock):
    state, clock, sleep = fake_clock

    def get(endpoint):
        if state["elapsed"] < 12:
            assert endpoint == f"commits/{FIRST}/statuses?per_page=100"
            return [_pending_intent()]
        return evidence[endpoint]

    assert batch.admit(FIRST, get, clock=clock, sleep=sleep) == {"full": False, "base": BASE, "run": 99}
    assert state["elapsed"] == 15


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("state", "success"),  # a closed compare URL is not a final proof
        ("creator", {"id": 7}),
        ("target_url", "https://example.invalid/intent"),
        ("created_at", "not-a-date"),
        ("created_at", "1969-12-31T23:56:59Z"),
        ("created_at", "1970-01-01T00:00:01Z"),
    ],
)
def test_closed_untrusted_malformed_or_expired_intent_does_not_wait(fake_clock, key, value):
    state, clock, sleep = fake_clock
    status = {**_pending_intent(), key: value}
    result = batch.admit(FIRST, lambda _: [status], clock=clock, sleep=sleep)
    assert result == {"full": True, "base": "", "run": ""}
    assert state["elapsed"] == 0
    assert state["waits"] == []


def test_queue_crash_intent_expires_into_full_ci(fake_clock):
    state, clock, sleep = fake_clock
    result = batch.admit(FIRST, lambda _: [_pending_intent()], clock=clock, sleep=sleep)
    assert result == {"full": True, "base": "", "run": ""}
    assert state["elapsed"] == 180


def test_closed_batch_stops_an_authorized_wait_immediately(fake_clock):
    state, clock, sleep = fake_clock

    def get(_):
        status = _pending_intent()
        if state["elapsed"] >= 10:
            status["state"] = "success"
        return [status]

    assert batch.admit(FIRST, get, clock=clock, sleep=sleep)["full"] is True
    assert state["elapsed"] == 10


def test_malformed_final_job_page_does_not_inherit_the_long_wait(evidence, fake_clock):
    state, clock, sleep = fake_clock
    page = evidence["actions/runs/99/attempts/1/jobs?per_page=100"]
    page["jobs"].insert(0, {})
    page["total_count"] += 1
    assert batch.admit(FIRST, evidence.__getitem__, clock=clock, sleep=sleep)["full"] is True
    assert state["elapsed"] == 0
