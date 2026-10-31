"""Offline tests; fake local subprocess only, no Claude or network."""

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).parents[2]

spec = importlib.util.spec_from_file_location("local_review", ROOT / "scripts/source_review.py")
assert spec
assert spec.loader
review = importlib.util.module_from_spec(spec)
with (
    patch.dict(
        os.environ,
        {
            "GITHUB_WORKSPACE": str(ROOT),
            "REVIEW_HEAD": "fa9fbc36bfcdb82ea6367952ca5b0cd9d64f8a18",
            "REVIEW_BASE": "e6c2e707726741e28ecc9360a3683fc00e968dfb",
            "RUNNER_TEMP": "/private/tmp",
        },
    ),
    patch.dict(sys.modules, {spec.name: review}),
):
    spec.loader.exec_module(review)


def test_report_requires_substance_and_rejects_contradictory_go():
    ok = {
        "verdict": "GO",
        "assessment": "x" * 80,
        "findings": [],
        "limitations": ["No tools or tests were run."],
    }
    review.validate_result(ok)
    for bad in (
        {**ok, "assessment": "x"},
        {**ok, "limitations": []},
        {
            **ok,
            "verdict": "GO",
            "findings": [
                {
                    "severity": "Required",
                    "path": "x.py",
                    "line": 1,
                    "problem": "x" * 20,
                    "impact": "y" * 20,
                }
            ],
        },
        {**ok, "unexpected": "x"},
    ):
        with pytest.raises(RuntimeError):
            review.validate_result(bad)


def test_fake_runner_has_no_tools_or_unrelated_credentials_and_binds_report(tmp_path, monkeypatch):
    fake = tmp_path / "fake_cli"
    assert not fake.exists()
    assert not fake.is_symlink()
    fake.write_text(
        "#!/usr/bin/python3\n"
        "import json,os,sys\n"
        'assert "GH_TOKEN" not in os.environ\n'
        'assert sys.argv[sys.argv.index("--tools")+1]==""\n'
        'assert all(f in sys.argv for f in ("--bare","--safe-mode","--restricted","--strict-mcp-config"))\n'
        'assert json.load(sys.stdin)["head"]\n'
        'r={"type":"result","subtype":"success","is_error":False,"permission_denials":[],'
        '"modelUsage":{"fake-model":{}},"structured_output":{"verdict":"GO","assessment":"x"*80,'
        '"findings":[],"limitations":["No tools or tests were run."]}}\n'
        "print(json.dumps(r))\n"
    )
    assert fake.resolve().is_relative_to(tmp_path.resolve())
    assert not fake.samefile(sys.executable)
    assert not fake.samefile(sys._base_executable)
    fake.chmod(0o700)
    monkeypatch.setattr(review, "CLI", fake)
    monkeypatch.setenv("GH_TOKEN", "dummy-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-api-key")
    names = ["x.py"]
    files = {f"{review.HEAD}:x.py": {"sha256": "0" * 64, "text": "x"}}
    diff = "diff"
    monkeypatch.setattr(review, "selected_sources", lambda: (names, files, diff))
    monkeypatch.setattr(
        review,
        "git",
        lambda *a: (review.HEAD if a[-1] == review.HEAD + "^{commit}" else review.BASE).encode(),
    )
    data = {
        "head": review.HEAD,
        "base": review.BASE,
        "changed_files": names,
        "diff": diff,
        "sources": files,
        "scope": review.SCOPE,
    }
    payload = json.dumps(data, ensure_ascii=True, separators=(",", ":")).encode()
    (tmp_path / "payload.json").write_bytes(payload)
    manifest = {
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "cli_sha256": hashlib.sha256(fake.read_bytes()).hexdigest(),
        "system_prompt_sha256": hashlib.sha256(review.SYSTEM.encode()).hexdigest(),
        "schema_sha256": hashlib.sha256(json.dumps(review.SCHEMA, sort_keys=True).encode()).hexdigest(),
    }
    review.execute(tmp_path, manifest)
    evidence = json.loads((tmp_path / "execution.json").read_text())
    assert evidence["reported_model_ids"] == ["fake-model"]
    assert (tmp_path / "result.json").stat().st_mode & 0o077 == 0
    assert "dummy-secret" not in (tmp_path / "result.json").read_text()
    with pytest.raises(RuntimeError, match="already exists"):
        review.execute(tmp_path, manifest)


def test_git_isolated_from_hostile_environment(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return (0, b"")

    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setattr(review, "bounded_output", fake_run)
    review.git("diff", "--no-ext-diff", "--no-textconv", "--name-only")
    args, kwargs = calls[0]
    assert "--no-textconv" in args
    assert "--no-ext-diff" in args
    assert kwargs["env"] == review.GIT_ENV
    assert "GIT_CONFIG_COUNT" not in kwargs["env"]
    assert "ANTHROPIC_API_KEY" not in kwargs["env"]
    assert kwargs["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
    assert kwargs["env"]["GIT_CONFIG_GLOBAL"] == "/dev/null"


def test_git_output_bounds_and_timeout():
    code, out = review.bounded_output([sys.executable, "-I", "-c", 'print("x"*100)'], limit=120, timeout=5)
    assert code == 0
    assert len(out) == 101
    with pytest.raises(RuntimeError, match="exceeds bound"):
        review.bounded_output([sys.executable, "-I", "-c", 'print("x"*1000)'], limit=32, timeout=5)
    with pytest.raises(TimeoutError):
        review.bounded_output(
            [sys.executable, "-I", "-c", "import time; time.sleep(10)"],
            limit=32,
            timeout=0.1,
        )


def test_present_tree_blob_read_failure_is_not_treated_as_absent(monkeypatch):
    monkeypatch.setattr(review, "CONTEXT", ())

    def fake_git(*args, **kwargs):
        if args[0] == "rev-parse":
            return (review.HEAD if args[1] == review.HEAD + "^{commit}" else review.BASE).encode()
        if args[0] == "diff":
            return b"x.py\0"
        if args[0] == "ls-tree":
            return b"100644 blob " + b"a" * 40 + b"\tx.py\0"
        raise AssertionError(args)

    monkeypatch.setattr(review, "git", fake_git)
    monkeypatch.setattr(review, "bounded_output", lambda *a, **kw: (1, b""))
    with pytest.raises(RuntimeError, match="Source size unavailable"):
        review.selected_sources()


def test_dispatch_review_is_explicit_and_release_body_is_preserved():
    import yaml

    root = Path(__file__).parents[2]
    proposed = yaml.safe_load((root / ".github/workflows/prepare-release.yml").read_text())
    assert proposed[True]["workflow_dispatch"]["inputs"]["mode"]["default"] == "release"
    release = proposed["jobs"]["pull-request"]
    assert release["if"] == "inputs.mode == 'release'"
    assert "cannot prepare a release" in release["steps"][0]["run"]
    assert any("gh workflow run ci.yml" in step.get("run", "") for step in release["steps"])
    assert any("gh pr merge" in step.get("run", "") for step in release["steps"])
    job = proposed["jobs"]["review"]
    assert job["if"] == "inputs.mode == 'source_review'"
    assert job["steps"][0]["name"] == "Check source-review dispatch authority before checkout"
    run_index = next(
        i for i, step in enumerate(job["steps"]) if step.get("name") == "Run bounded independent source review"
    )
    setup_index = next(
        i for i, step in enumerate(job["steps"]) if step.get("uses", "").startswith("astral-sh/setup-uv@")
    )
    assert setup_index < run_index
    assert job["steps"][run_index]["run"] == "make independent-source-review"
    assert all("ANTHROPIC_API_KEY" not in step.get("env", {}) for step in job["steps"][:run_index])
    assert not any("CI OK" in str(step) for step in job["steps"])
    assert "ANTHROPIC_API_KEY" in job["steps"][run_index]["env"]


def test_lock_omission_requires_exact_only_core_version_change():
    old = 'prefix\n[[package]]\nname = "heinzel"\nversion = "0.9.7"\nrest\n'
    new = old.replace("0.9.7", "0.9.8")
    files = {
        f"{review.BASE}:uv.lock": {"text": old, "sha256": "a"},
        f"{review.HEAD}:uv.lock": {"text": new, "sha256": "b"},
    }
    review.compress_metadata_only_lock(files)
    assert files[f"{review.BASE}:uv.lock"]["text"] is None
    assert "byte-exact" in files[f"{review.HEAD}:uv.lock"]["omitted_reason"]
    files = {
        f"{review.BASE}:uv.lock": {"text": old, "sha256": "a"},
        f"{review.HEAD}:uv.lock": {"text": new + "other change", "sha256": "b"},
    }
    review.compress_metadata_only_lock(files)
    assert files[f"{review.BASE}:uv.lock"]["text"] == old
    assert files[f"{review.HEAD}:uv.lock"]["text"] == new + "other change"


def test_wrong_dispatch_ref_fails_before_any_network(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REF", "refs/heads/untrusted")
    monkeypatch.setattr(review, "current_pr", lambda: pytest.fail("must not access network"))
    with pytest.raises(RuntimeError, match="Unapproved dispatch authority"):
        review.github_review(tmp_path)


def test_stale_pr_refuses_before_report_is_printed(monkeypatch, capsys):
    def stale():
        msg = "PR identity changed"
        raise RuntimeError(msg)

    monkeypatch.setattr(review, "current_pr", stale)
    with pytest.raises(RuntimeError, match="PR identity changed"):
        review.publish_after_recheck(
            {"verdict": "GO", "assessment": "secret"},
            ["fake-model"],
            {"payload_sha256": "a"},
        )
    assert capsys.readouterr().out == ""


def test_inherited_stdout_descendant_does_not_extend_capture_deadline():
    """A descendant retaining stdout cannot hold the review capture open indefinitely."""
    import time

    program = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(3)']); "
        "print('done',flush=True)"
    )
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        review.bounded_output([sys.executable, "-I", "-c", program], limit=128, timeout=0.2)
    assert time.monotonic() - start < 2.0


@pytest.mark.parametrize("row", ["closed main", "open other", "malformed"])
def test_current_pr_refuses_closed_retargeted_or_malformed_state(row, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GH_TOKEN", "dummy")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    raw = f"{row} {review.HEAD} {review.BASE} owner/repo owner/repo".encode()
    monkeypatch.setattr(review, "bounded_output", lambda *args, **kwargs: (0, raw))
    with pytest.raises(RuntimeError, match="PR identity changed"):
        review.current_pr()


def test_mac_diagnostics_are_read_only_scoped_and_keep_test_budgets():
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/prepare-release.yml").read_text())
    options = workflow[True]["workflow_dispatch"]["inputs"]["mode"]["options"]
    assert options == ["release", "source_review", "diagnostic_unit315", "diagnostic_e2e"]
    assert "inputs.mode == 'release' && 'release'" in workflow["concurrency"]["group"]
    validation = workflow["jobs"]["dispatch-validation"]
    guard = validation["steps"][0]["run"]
    assert "Unsupported dispatch mode" in guard
    assert 'test "$GITHUB_REF" !=' in guard
    for name in ("pull-request", "review", "diagnostic"):
        assert workflow["jobs"][name]["needs"] == "dispatch-validation"
    diagnostic = workflow["jobs"]["diagnostic"]
    assert diagnostic["permissions"] == {"contents": "read", "pull-requests": "read"}
    steps = diagnostic["steps"]
    assert steps[0]["name"] == "Verify approved target before checkout"
    assert "216c0500e9045ec2615e44abd0dec56a598f35e3" in steps[0]["run"]
    assert "5d4a5e378b5ed0dbf5795702f7e342c98df7afc7" in steps[0]["run"]
    assert ".state,.base.ref" in steps[0]["run"]
    assert ".base.repo.full_name" in steps[0]["run"]
    assert steps[1]["with"]["ref"] == "${{ inputs.target_head }}"
    assert steps[1]["with"]["persist-credentials"] is False
    assert "ANTHROPIC_API_KEY" not in str(diagnostic)
    for step in steps:
        if step.get("name", "").startswith("Run exact "):
            assert step["env"]["VERBOSE"] == "1"
            assert "--durations=20" in step["env"]["PYTEST_ADDOPTS"]
            assert "sed -l -E" in step["run"]
            assert "#token=" in step["run"]
            assert "<redacted>" in step["run"]
            if step["if"] == "inputs.mode == 'diagnostic_unit315'":
                assert "uv run --quiet --script scripts/steps.py" in step["run"]
                assert "tests=env -u VERBOSE uv run pytest -m '(unit or integration) and not perf'" in step["run"]
                assert "-n 4 -x -q --session-timeout=420" in step["run"]
    final = workflow["jobs"]["diagnostic-identity"]
    assert final["runs-on"] == "ubuntu-latest"
    assert final["needs"] == "diagnostic"
    assert "DIAG_RESULT" in final["steps"][0]["run"]
    makefile = (ROOT / "Makefile").read_text()
    assert "-n 4 -x -q --session-timeout=420" in makefile
    assert "-m e2e -n 3 -q --session-timeout=600" in makefile


def test_current_pr_accepts_only_exact_open_main_and_both_repository_identities(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GH_TOKEN", "dummy")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    calls = []

    def observe(command, **_kwargs):
        calls.append(command)
        return 0, f"open main {review.HEAD} {review.BASE} owner/repo owner/repo".encode()

    monkeypatch.setattr(review, "bounded_output", observe)
    review.current_pr()
    assert "repos/owner/repo/pulls/310" in calls[0]
    assert ".state,.base.ref" in calls[0][-1]
    assert ".base.repo.full_name" in calls[0][-1]
    monkeypatch.setattr(
        review,
        "bounded_output",
        lambda *_args, **_kwargs: (0, f"open main {review.HEAD} {review.BASE} owner/repo foreign/repo".encode()),
    )
    with pytest.raises(RuntimeError, match="PR identity changed"):
        review.current_pr()


@pytest.mark.parametrize(
    ("mode", "ref", "actor", "expected"),
    [
        ("bogus", "refs/heads/main", "owner", False),
        ("release", "refs/heads/private/independent-source-review-310-r3", "owner", False),
        ("source_review", "refs/heads/private/independent-source-review-310-r3", "other", False),
        ("release", "refs/heads/main", "owner", True),
        ("diagnostic_unit315", "refs/heads/private/independent-source-review-310-r3", "owner", True),
    ],
)
def test_dispatch_validation_fails_closed(mode, ref, actor, expected):
    import subprocess

    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/prepare-release.yml").read_text())
    script = workflow["jobs"]["dispatch-validation"]["steps"][0]["run"]
    environment = {
        "MODE": mode,
        "GITHUB_REF": ref,
        "ACTOR": actor,
        "OWNER": "owner",
        "GITHUB_SHA": "trusted",
        "TRUSTED_COMMIT": "trusted",
    }
    result = subprocess.run(["/bin/bash", "-c", script], env=environment, capture_output=True, timeout=5, check=False)
    assert (result.returncode == 0) is expected


@pytest.mark.parametrize("field", ["HEAD", "BASE"])
def test_wrong_exact_target_refuses_before_github(field, monkeypatch):
    monkeypatch.setattr(review, field, "f" * 40)
    monkeypatch.setattr(review, "bounded_output", lambda *_args, **_kwargs: pytest.fail("must not query GitHub"))
    with pytest.raises(RuntimeError, match="PR identity changed"):
        review.current_pr()


def test_source_review_fetches_only_310_after_authority_checks(tmp_path, monkeypatch):
    trusted = "a" * 40
    monkeypatch.setenv("GITHUB_REF", "refs/heads/private/independent-source-review-310-r3")
    monkeypatch.setenv("GITHUB_ACTOR", "owner")
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "owner")
    monkeypatch.setenv("GITHUB_SHA", trusted)
    monkeypatch.setenv("TRUSTED_COMMIT", trusted)
    monkeypatch.setattr(review, "git", lambda *_args: trusted.encode())
    monkeypatch.setattr(review, "current_pr", lambda: None)
    calls = []

    def fail_fetch(command, **_kwargs):
        calls.append(command)
        return 1, b""

    monkeypatch.setattr(review, "bounded_output", fail_fetch)
    with pytest.raises(RuntimeError, match="PR target unavailable"):
        review.github_review(tmp_path)
    assert calls[0][-1] == "refs/pull/310/head:refs/review/target"


def test_source_review_rejects_wrong_owner_before_network(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REF", "refs/heads/private/independent-source-review-310-r3")
    monkeypatch.setenv("GITHUB_ACTOR", "other")
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "owner")
    monkeypatch.setattr(review, "current_pr", lambda: pytest.fail("must not access network"))
    with pytest.raises(RuntimeError, match="Unapproved dispatch authority"):
        review.github_review(tmp_path)


@pytest.mark.parametrize(
    "ref", ["refs/heads/private/independent-source-review", "refs/heads/private/independent-source-review-307"]
)
def test_old_helper_refs_are_not_r2_review_authority(ref, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REF", ref)
    monkeypatch.setenv("GITHUB_ACTOR", "owner")
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "owner")
    monkeypatch.setattr(review, "current_pr", lambda: pytest.fail("must not access network"))
    with pytest.raises(RuntimeError, match="Unapproved dispatch authority"):
        review.github_review(tmp_path)


def test_source_review_rejects_changed_trusted_commit_before_network(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REF", "refs/heads/private/independent-source-review-310-r3")
    monkeypatch.setenv("GITHUB_ACTOR", "owner")
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "owner")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("TRUSTED_COMMIT", "b" * 40)
    monkeypatch.setattr(review, "current_pr", lambda: pytest.fail("must not access network"))
    with pytest.raises(RuntimeError, match="Trusted review ref changed"):
        review.github_review(tmp_path)


def test_review_context_contains_playbook_authority_dependencies():
    assert {
        "scripts/steps.py",
        "tests/e2e/docs_test.py",
        "src/heinzel/modules/heinzel/_contract_review.py",
        "src/heinzel/modules/heinzel/_interactive_turn.py",
        "src/heinzel/modules/heinzel/_playbooks.py",
        "src/heinzel/modules/heinzel/_session_crud.py",
        "src/heinzel/modules/heinzel/_session_repository.py",
        "src/heinzel/modules/heinzel/_user_chat.py",
    } <= set(review.CONTEXT)
