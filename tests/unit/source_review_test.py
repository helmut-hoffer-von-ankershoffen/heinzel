import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("source_review", REPO / "scripts" / "source_review.py")
assert spec
assert spec.loader
source_review = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = source_review
spec.loader.exec_module(source_review)

HEAD, BASE, TRUSTED = "a" * 40, "b" * 40, "c" * 40
REPOSITORY = "owner/repo"


def make_config(tmp_path, **overrides):
    env = {
        "GITHUB_WORKSPACE": str(tmp_path),
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_REPOSITORY": REPOSITORY,
        "REVIEW_PR": "7",
        "REVIEW_HEAD": HEAD,
        "REVIEW_BASE": BASE,
        "REVIEW_TIER": "full",
        "TRUSTED_COMMIT": TRUSTED,
        "GITHUB_REF": source_review.PROTECTED_REF,
        "GITHUB_SHA": TRUSTED,
        "GITHUB_ACTOR": "owner",
        "GITHUB_REPOSITORY_OWNER": "owner",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_WORKFLOW_REF": f"{REPOSITORY}/.github/workflows/source-review.yml@{source_review.PROTECTED_REF}",
    }
    env.update(overrides)
    return source_review.Config.from_env(env)


def pull(**changes):
    value = {
        "state": "open",
        "draft": False,
        "merged": False,
        "head": {"sha": HEAD, "repo": {"full_name": REPOSITORY}},
        "base": {"sha": BASE, "ref": "main", "repo": {"full_name": REPOSITORY}},
    }
    for key, new in changes.items():
        if isinstance(new, dict):
            value[key] = {**value[key], **new}
        else:
            value[key] = new
    return value


def fake_github(monkeypatch, pr, ref_sha=TRUSTED):
    def read(_config, endpoint, *_flags):
        return pr if endpoint.startswith("pulls/") else {"object": {"sha": ref_sha}}

    monkeypatch.setattr(source_review, "gh_json", read)


# The guard


def test_live_snapshot_accepts_exactly_the_dispatch(tmp_path, monkeypatch):
    fake_github(monkeypatch, pull())
    assert source_review.live_snapshot(make_config(tmp_path))["head_sha"] == HEAD


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "closed"},
        {"draft": True},
        {"merged": True},
        {"head": {"sha": "d" * 40}},
        {"base": {"sha": "d" * 40}},
        {"base": {"ref": "other"}},
        {"head": {"repo": {"full_name": "fork/repo"}}},
        {"head": {"repo": None}},
    ],
)
def test_live_snapshot_fails_closed_when_the_pull_request_differs(tmp_path, monkeypatch, changes):
    fake_github(monkeypatch, pull(**changes))
    with pytest.raises(source_review.ReviewError, match="changed"):
        source_review.live_snapshot(make_config(tmp_path))


def test_live_snapshot_fails_closed_when_the_protected_ref_moved(tmp_path, monkeypatch):
    fake_github(monkeypatch, pull(), ref_sha="d" * 40)
    with pytest.raises(source_review.ReviewError, match="protected_ref_sha"):
        source_review.live_snapshot(make_config(tmp_path))


@pytest.mark.parametrize(
    "override",
    [
        {"GITHUB_REF": "refs/heads/main"},
        {"GITHUB_SHA": "d" * 40},
        {"GITHUB_ACTOR": "someone-else"},
        {"GITHUB_EVENT_NAME": "push"},
        {"GITHUB_WORKFLOW_REF": f"{REPOSITORY}/.github/workflows/source-review.yml@refs/heads/main"},
    ],
)
def test_authority_requires_the_protected_ref_owner_and_trusted_commit(tmp_path, monkeypatch, override):
    monkeypatch.setattr(source_review, "git", lambda *_a, **_k: f"{TRUSTED}\n".encode())
    with pytest.raises(source_review.ReviewError, match="authority"):
        source_review.check_authority(make_config(tmp_path, **override))


def test_authority_accepts_the_dispatch_and_checks_the_checked_out_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(source_review, "git", lambda *_a, **_k: f"{TRUSTED}\n".encode())
    source_review.check_authority(make_config(tmp_path))
    monkeypatch.setattr(source_review, "git", lambda *_a, **_k: f"{'d' * 40}\n".encode())
    with pytest.raises(source_review.ReviewError, match="authority"):
        source_review.check_authority(make_config(tmp_path))


@pytest.mark.parametrize(
    "override",
    [
        {"REVIEW_HEAD": "abc"},
        {"REVIEW_PR": "0"},
        {"REVIEW_PR": "7; rm"},
        {"REVIEW_TIER": "none"},
        {"REVIEW_BASE": "B" * 40},
    ],
)
def test_malformed_dispatch_input_is_refused(tmp_path, override):
    with pytest.raises(source_review.ReviewError, match="Malformed"):
        make_config(tmp_path, **override)


def test_a_dispatched_tier_may_not_be_below_the_needed_one(tmp_path):
    light, full = make_config(tmp_path, REVIEW_TIER="light"), make_config(tmp_path)
    source_review.check_tier(light, ["x"], lambda _paths: "light")
    source_review.check_tier(full, ["x"], lambda _paths: "light")
    source_review.check_tier(light, ["x"], lambda _paths: "none")
    with pytest.raises(source_review.ReviewError, match="below"):
        source_review.check_tier(light, ["x"], lambda _paths: "full")


# Real Git objects, no network


def git_in(directory, *args):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(
        ["git", "-C", str(directory), *args], check=True, capture_output=True, text=True, env=env, timeout=60
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    # A pre-push hook exports GIT_DIR and GIT_INDEX_FILE: without this, the test would write into the real repository.
    for key in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(key)
    root = tmp_path / "repo"
    root.mkdir()
    git_in(root, "init", "-q", "-b", "main")
    git_in(root, "config", "user.email", "t@example.org")
    git_in(root, "config", "user.name", "T")
    (root / "INTENT.md").write_text("intent\n")
    (root / "old.py").write_text("print(1)\n")
    (root / "gone.py").write_text("print(2)\n")
    git_in(root, "add", ".")
    git_in(root, "commit", "-qm", "base")
    base = git_in(root, "rev-parse", "HEAD")
    (root / "old.py").rename(root / "new.py")
    (root / "gone.py").unlink()
    (root / "added.txt").write_text("hello\n")
    git_in(root, "add", "-A")
    git_in(root, "commit", "-qm", "head")
    return root, base, git_in(root, "rev-parse", "HEAD")


def repo_config(root, base, head):
    return make_config(
        root, GITHUB_WORKSPACE=str(root), RUNNER_TEMP=str(root.parent), REVIEW_HEAD=head, REVIEW_BASE=base
    )


def test_payload_holds_both_sides_of_renames_and_deletions_and_never_the_checkout(repo):
    root, base, head = repo
    payload, manifest, names = source_review.build_payload(repo_config(root, base, head))
    assert sorted(names) == ["added.txt", "gone.py", "new.py", "old.py"]
    data = json.loads(payload)
    assert data["sources"][f"{base}:gone.py"]["text"] == "print(2)\n"
    assert data["sources"][f"{head}:gone.py"] is None
    assert data["sources"][f"{base}:new.py"] is None
    assert data["sources"][f"{head}:new.py"]["text"] == "print(1)\n"
    assert data["sources"][f"{base}:INTENT.md"]["text"] == "intent\n"
    assert data["sources"][f"{head}:docs/CODING_STANDARDS.md"] is None  # optional context may be absent
    assert manifest["payload_sha256"] == source_review.hashlib.sha256(payload).hexdigest()
    assert payload == source_review.build_payload(repo_config(root, base, head))[0]


def test_binary_and_oversized_sources_are_refused(repo, monkeypatch):
    root, base, head = repo
    (root / "blob.bin").write_bytes(b"a\0b")
    git_in(root, "add", "-A")
    git_in(root, "commit", "-qm", "binary")
    newer = git_in(root, "rev-parse", "HEAD")
    with pytest.raises(source_review.ReviewError, match="binary"):
        source_review.build_payload(repo_config(root, base, newer))
    monkeypatch.setattr(source_review, "MAX_PAYLOAD", 100)
    with pytest.raises(source_review.ReviewError, match="exceeds"):
        source_review.build_payload(repo_config(root, base, head))


def test_a_large_change_is_refused(repo, monkeypatch):
    root, base, head = repo
    monkeypatch.setattr(source_review, "MAX_FILES", 2)
    with pytest.raises(source_review.ReviewError, match="Unsafe or empty"):
        source_review.build_payload(repo_config(root, base, head))


# The model


def test_the_model_gets_no_tools_and_the_unchanged_limits():
    command = source_review.model_command(Path("claude"), "light")
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert {"--bare", "--safe-mode", "--restricted", "--strict-mcp-config", "--no-session-persistence"} <= set(command)
    assert command[command.index("--max-budget-usd") + 1] == "3"
    assert (source_review.MAX_PAYLOAD, source_review.MAX_OUTPUT, source_review.MAX_FILES) == (3_000_000, 1_000_000, 100)
    assert (source_review.MODEL_TIMEOUT, source_review.MAX_CLI_SIZE) == (240, 300_000_000)
    assert source_review.system_prompt("full") != source_review.system_prompt("light")


def test_the_model_sees_only_its_key(tmp_path, monkeypatch):
    fake = tmp_path / "fake_cli"
    fake.write_text(
        "#!/usr/bin/python3\nimport json,os,sys\n"
        "assert 'GH_TOKEN' not in os.environ and os.environ['ANTHROPIC_API_KEY']=='k'\n"
        "assert json.load(sys.stdin)['head']\n"
        "print(json.dumps({'type':'result'}))\n"
    )
    fake.chmod(0o700)
    payload = tmp_path / "payload.json"
    payload.write_text('{"head": "x"}')
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(source_review, "model_command", lambda executable, _tier: [str(executable)])
    result = source_review.invoke_model(make_config(tmp_path), fake, payload)
    assert result == {"type": "result"}


REPORT = {"verdict": "GO", "assessment": "x" * 80, "findings": [], "limitations": ["No tools or tests were run."]}


def result_with(report):
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "permission_denials": [],
        "modelUsage": {"model-x": {}},
        "structured_output": report,
    }


def finding(**changes):
    return {"severity": "Optional", "path": "a.py", "line": 1, "problem": "p" * 20, "impact": "i" * 20, **changes}


def test_a_complete_report_is_accepted():
    report, models = source_review.check_result(result_with({**REPORT, "findings": [finding()]}), ["a.py"])
    assert report["verdict"] == "GO"
    assert models == ["model-x"]


@pytest.mark.parametrize(
    "bad",
    [
        {**REPORT, "assessment": "short"},
        {**REPORT, "limitations": []},
        {**REPORT, "extra": 1},
        {**REPORT, "verdict": "MAYBE"},
        {**REPORT, "findings": [finding(severity="Required")]},
        {**REPORT, "findings": [finding(line=0)]},
        {**REPORT, "findings": [finding(path="unsupplied.py")]},
    ],
)
def test_incomplete_contradictory_or_unsupplied_reports_are_refused(bad):
    with pytest.raises(source_review.ReviewError):
        source_review.check_result(result_with(bad), ["a.py"])


@pytest.mark.parametrize(
    "change", [{"is_error": True}, {"permission_denials": [{"x": 1}]}, {"modelUsage": {}}, {"subtype": "error"}]
)
def test_a_denied_or_failed_run_is_not_a_verdict(change):
    with pytest.raises(source_review.ReviewError, match="incomplete"):
        source_review.check_result({**result_with(REPORT), **change}, [])


def test_bounded_children_stop_on_overflow_and_deadline(tmp_path):
    env = {"PATH": os.environ["PATH"]}
    code, out = source_review.run_bounded([sys.executable, "-c", "print('ok')"], limit=100, timeout=20, env=env)
    assert (code, out) == (0, b"ok\n")
    with pytest.raises(source_review.ReviewError, match="exceeds"):
        source_review.run_bounded([sys.executable, "-c", "print('x'*1000)"], limit=100, timeout=20, env=env)
    with pytest.raises(source_review.ReviewError, match="deadline"):
        source_review.run_bounded([sys.executable, "-c", "import time; time.sleep(30)"], limit=100, timeout=1, env=env)


# The workflow and the dispatch command


WORKFLOW = yaml.safe_load((REPO / ".github" / "workflows" / "source-review.yml").read_text())
TRIGGERS = WORKFLOW.get("on") or WORKFLOW[True]


def test_workflow_is_dispatch_only_with_no_default_permissions():
    assert list(TRIGGERS) == ["workflow_dispatch"]
    assert WORKFLOW["permissions"] == {}
    assert set(TRIGGERS["workflow_dispatch"]["inputs"]) == {
        "pr",
        "target_head",
        "target_base",
        "trusted_commit",
        "tier",
    }
    assert all(spec["required"] for spec in TRIGGERS["workflow_dispatch"]["inputs"].values())
    assert TRIGGERS["workflow_dispatch"]["inputs"]["tier"]["options"] == ["full", "light"]


def test_the_authority_check_comes_first_and_names_the_ref_the_script_names():
    steps = WORKFLOW["jobs"]["review"]["steps"]
    assert "source-review dispatch authority" in steps[0]["run"]
    assert source_review.PROTECTED_REF in steps[0]["run"]
    assert f"@{source_review.PROTECTED_REF}" in steps[0]["run"]
    assert steps[1]["uses"].startswith("actions/checkout@")
    assert steps[1]["with"] == {"ref": "${{ github.sha }}", "fetch-depth": 0, "persist-credentials": False}
    secret_steps = [i for i, step in enumerate(steps) if "secrets." in json.dumps(step)]
    assert secret_steps == [3]
    assert WORKFLOW["jobs"]["review"]["environment"] == "source-review"
    assert WORKFLOW["jobs"]["review"]["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert source_review.PROTECTED_REF.removeprefix("refs/heads/") in json.dumps(WORKFLOW["jobs"]["identity"])


def test_the_identity_job_has_no_secret_and_runs_after_failures():
    job = WORKFLOW["jobs"]["identity"]
    assert job["if"] == "always()"
    assert "secrets." not in json.dumps(job)
    assert job["permissions"] == {"contents": "read", "pull-requests": "read"}


def test_untrusted_inputs_reach_scripts_only_through_env():
    for job in WORKFLOW["jobs"].values():
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), step["run"]


def fake_gh(tmp_path, **answers):
    """A gh that answers the reads review.sh makes and logs a dispatch."""
    pr = answers.get("pr", pr_row())
    main, ref, protected = answers.get("main", BASE), answers.get("ref", TRUSTED), answers.get("protected", "true")
    files = answers.get("files", '["docs/a.md"]')
    log = tmp_path / "dispatch.log"
    script = tmp_path / "bin" / "gh"
    script.parent.mkdir(exist_ok=True)
    script.write_text(
        "#!/bin/bash\n"
        'args="$*"\n'
        'case "$args" in\n'
        f"  'api --paginate repos/owner/repo/pulls/7/files'*) printf '%b\\n' '{files}' ;;\n"
        f"  'api repos/owner/repo/pulls/7'*) printf '{pr}\\n' ;;\n"
        f"  'api repos/owner/repo/git/ref/heads/main'*) echo {main} ;;\n"
        f"  'api repos/owner/repo/branches/trusted/source-review'*) printf '{ref}\\t{protected}\\n' ;;\n"
        f"  'workflow run'*) echo \"$args\" >> {log} ;;\n"
        "  'run list'*) echo https://example.org/run/1 ;;\n"
        '  *) echo "unexpected gh $args" >&2; exit 1 ;;\n'
        "esac\n"
    )
    script.chmod(0o755)
    return script.parent, log


def run_review(tmp_path, bin_dir, *, trusted=TRUSTED, pr="7"):
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "REVIEW_TRUSTED": trusted,
        "REVIEW_REPO": REPOSITORY,
    }
    return subprocess.run(
        ["bash", str(REPO / "scripts" / "review.sh"), pr],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
        timeout=120,
    )


def pr_row(**fields):
    row = {"state": "open", "draft": "false", "merged": "false", "base_ref": "main", "head": HEAD, "base": BASE,
           "head_repo": REPOSITORY, "base_repo": REPOSITORY, "changed": "1", **fields}  # fmt: skip
    return "\\t".join(row.values())


@pytest.mark.parametrize(
    ("fake", "message"),
    [
        ({"pr": pr_row(state="closed")}, "not open"),
        ({"pr": pr_row(draft="true")}, "draft"),
        ({"pr": pr_row(merged="true")}, "merged"),
        ({"pr": pr_row(base_ref="dev")}, "not main"),
        ({"pr": pr_row(head_repo="fork/repo")}, "forks"),
        ({"pr": pr_row(head_repo="")}, "forks"),
        ({"main": "d" * 40}, "update the branch"),
        ({"ref": "d" * 40}, "REVIEW_TRUSTED"),
        ({"protected": "false"}, "no branch protection"),
        ({"pr": pr_row(changed="3")}, "incomplete"),
    ],
)
def test_review_command_refuses_before_dispatching(tmp_path, fake, message):
    bin_dir, log = fake_gh(tmp_path, **{"pr": pr_row(), **fake})
    done = run_review(tmp_path, bin_dir)
    assert done.returncode == 2, done.stdout + done.stderr
    assert message in done.stderr
    assert not log.exists()


def test_review_command_needs_the_trusted_commit(tmp_path):
    bin_dir, log = fake_gh(tmp_path, pr=pr_row())
    done = run_review(tmp_path, bin_dir, trusted="")
    assert done.returncode == 2
    assert "REVIEW_TRUSTED" in done.stderr
    assert not log.exists()


def test_review_command_spends_nothing_on_documentation_only_changes(tmp_path):
    bin_dir, log = fake_gh(tmp_path, pr=pr_row(), files='["docs/a.md"]')
    done = run_review(tmp_path, bin_dir)
    assert done.returncode == 0, done.stderr
    assert "tier none" in done.stdout
    assert not log.exists()


def test_review_command_dispatches_the_exact_inputs_for_a_risky_change(tmp_path):
    bin_dir, log = fake_gh(tmp_path, pr=pr_row(changed="2"), files='["docs/a.md"]\\n["docs/b.md","scripts/release.sh"]')
    done = run_review(tmp_path, bin_dir)
    assert done.returncode == 0, done.stderr
    assert "tier full" in done.stdout
    assert done.stdout.strip().endswith("https://example.org/run/1")
    dispatched = log.read_text()
    for fragment in (
        "--ref trusted/source-review",
        "-f pr=7",
        f"-f target_head={HEAD}",
        f"-f target_base={BASE}",
        f"-f trusted_commit={TRUSTED}",
        "-f tier=full",
    ):
        assert fragment in dispatched


def regeneration(monkeypatch, runs):
    monkeypatch.setattr(source_review, "gh_json", lambda *_args, **_kwargs: {"check_runs": runs})


def passing(head, **changes):
    run = {
        "name": source_review.REGENERATION_CHECK,
        "head_sha": head,
        "status": "completed",
        "conclusion": "success",
        "app": {"slug": "github-actions"},
    }
    return {**run, **changes}


@pytest.fixture
def repo_with_pair(repo):
    root, base, _ = repo
    for path in source_review.GENERATED_PAIR:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("generated\n" * 100)
    git_in(root, "add", ".")
    git_in(root, "commit", "-qm", "pair")
    return root, base, git_in(root, "rev-parse", "HEAD")


def test_a_regenerated_pair_is_omitted_with_its_digests(repo_with_pair, monkeypatch):
    root, base, head = repo_with_pair
    regeneration(monkeypatch, [passing(head)])
    payload, manifest, names = source_review.build_payload(repo_config(root, base, head))
    data = json.loads(payload)
    assert manifest["generated_pair_omitted"] is True
    assert set(source_review.GENERATED_PAIR) <= set(names)
    record = data["sources"][f"{head}:web/openapi.json"]
    assert record["omitted"] == "generated"
    assert "text" not in record
    assert "generated\\n" not in data["diff"]
    assert "regenerated" in data["scope"]


@pytest.mark.parametrize(
    "changes",
    [
        {"conclusion": "failure"},
        {"status": "in_progress", "conclusion": None},
        {"head_sha": "0" * 40},
        {"name": "Something else"},
        {"app": {"slug": "someone-else"}},
    ],
)
def test_without_proof_on_this_head_the_pair_stays_in_the_payload(repo_with_pair, monkeypatch, changes):
    root, base, head = repo_with_pair
    regeneration(monkeypatch, [passing(head, **changes)])
    payload, manifest, _ = source_review.build_payload(repo_config(root, base, head))
    assert manifest["generated_pair_omitted"] is False
    assert "text" in json.loads(payload)["sources"][f"{head}:web/openapi.json"]


def test_one_failed_attempt_is_no_proof(repo_with_pair, monkeypatch):
    root, base, head = repo_with_pair
    regeneration(monkeypatch, [passing(head), passing(head, conclusion="failure")])
    assert source_review.build_payload(repo_config(root, base, head))[1]["generated_pair_omitted"] is False


def test_a_change_without_the_pair_asks_github_nothing(repo, monkeypatch):
    root, base, head = repo
    monkeypatch.setattr(source_review, "gh_json", lambda *_a, **_k: pytest.fail("no read is needed"))
    assert source_review.build_payload(repo_config(root, base, head))[1]["generated_pair_omitted"] is False
