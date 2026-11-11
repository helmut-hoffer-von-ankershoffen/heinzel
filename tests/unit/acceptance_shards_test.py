"""Acceptance sharding preserves every case and refuses mixed or incomplete hosted-run evidence."""

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "scripts/acceptance"
SPEC = importlib.util.spec_from_file_location("acceptance_reducer", DIRECTORY / "reduce.py")
assert SPEC
assert SPEC.loader
reducer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reducer)
harness = reducer.HARNESS
CI = {
    "repository": "example/project",
    "sha": "a" * 40,
    "run_id": "123",
    "run_attempt": "1",
    "workflow": "Acceptance sharding study",
    "event": "workflow_dispatch",
}
WHEEL, PROFILE = "b" * 64, "c" * 64


def receipts(candidate=False, runner_os="macOS"):
    args = SimpleNamespace(
        candidate_wheel_url=candidate, diagnostic_explicit_profile=False, candidate_wheel=None, bootstrap="literal"
    )
    result = []
    for name, selected in harness.SHARDS.items():
        cases = {
            ident: {
                "name": harness.CASES[ident],
                "status": "UNPROVABLE" if ident in ("11", "12") else "PASS",
                "forbidden_error_hits": [],
            }
            for ident in selected
        }
        if candidate:
            for ident in set(selected) & {"1", "5", "13"}:
                cases[ident]["status"] = "SKIPPED"
        result.append(
            {
                "schema": 2,
                "shard": {"name": name, "cases": list(selected)},
                "ci": CI | {"runner_os": runner_os, "runner_arch": "ARM64" if runner_os == "macOS" else "X64"},
                "harness_sha256": harness.file_sha256(DIRECTORY / "run_acceptance.py"),
                "guardian_sha256": harness.GUARDIAN_SHA256,
                "provenance_sha256": harness.PROVENANCE_SHA256,
                "mode": "candidate_release" if candidate else "acceptance_literal",
                "public_path_proof": not candidate,
                "expectation_supplied": True,
                "candidate_release" if candidate else "published": {
                    "expect_version": "1.2.3",
                    "expect_app_version": "1.2.3",
                    "core_wheel_sha256": WHEEL,
                    "profile_sha256": PROFILE,
                    "artifacts": {"heinzel": {"sha256": WHEEL}},
                },
                "preflight_seconds": 2.5,
                "started": "2026-01-01T00:00:00+0000",
                "finished": "2026-01-01T00:01:00+0000",
                "isolation": {
                    "live_untouched": True,
                    "logs_containing_session_tokens": 0,
                    "live_before": {"port": []},
                    "live_after": {"port": []},
                },
                "host": {"service_manager": {"applicable": runner_os == "macOS"}},
                "proof_root": f"/proof/hz-acceptance-{name}",
                "limits": harness.LIMITS,
                "cases": cases,
                "verdict": harness.verdict(args, cases),
                "lifecycle": {
                    "bootstrap": "release-candidate" if candidate else "literal",
                    "candidate": None,
                    "update_target": {"version": "1.2.3"},
                    "from_app": {"version": "1.2.2"} if name == "update" else None,
                },
            }
        )
    return result


def reduce(values, runner_os="macOS"):
    return reducer.reduce_receipts(values, runner_os, CI, WHEEL, PROFILE)


def test_partition_contains_every_existing_case_exactly_once():
    selected = [ident for cases in harness.SHARDS.values() for ident in cases]
    assert len(selected) == len(set(selected)) == 13
    assert set(selected) == set(harness.CASES)


@pytest.mark.parametrize("candidate", [True, False])
@pytest.mark.parametrize("runner_os", ["macOS", "Linux"])
def test_complete_isolated_shards_retain_candidate_semantics_and_all_raw_receipts(candidate, runner_os):
    values = receipts(candidate, runner_os)
    original = copy.deepcopy(values)
    result = reduce(values, runner_os)
    assert result["verdict"] == "STUDY_COMPLETE"
    assert result["combined_case_verdict"] == ("CANDIDATE_" if candidate else "") + "ACCEPTED_EXCEPT_UNPROVABLE"
    assert result["public_path_proof"] is False
    assert result["release_eligible"] is False
    assert result["schema"] == "acceptance-sharding-study/v1"
    assert result["mode"] == "acceptance_sharding_study"
    assert harness.exit_code(result["verdict"]) != 0
    assert result["target_runner_os"] == runner_os
    assert set(result["cases"]) == set(harness.CASES)
    assert result["lifecycle"]["from_app"] == {"version": "1.2.2"}
    assert not {"proof_root", "host", "ci", "preflight_seconds", "started", "finished"} & result.keys()
    assert list(result["receipts"].values()) == values == original
    assert result["timing"]["harness_wall_span_seconds"] == 60
    assert {s["preflight_seconds"] for s in result["timing"]["shards"].values()} == {2.5}


@pytest.mark.parametrize("field", reducer.CI_KEYS)
def test_cross_run_attempt_source_workflow_and_repository_mixing_is_refused(field):
    values = receipts()
    values[0]["ci"][field] = "different"
    with pytest.raises(ValueError, match="Mixed CI"):
        reduce(values)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.pop(),
        lambda r: r.append(copy.deepcopy(r[0])),
        lambda r: r.__setitem__(1, copy.deepcopy(r[0])),
        lambda r: r[0]["cases"].pop("1"),
        lambda r: r[1]["cases"].update({"1": r[0]["cases"]["1"]}),
        lambda r: r[0]["cases"]["1"].update(status="UNKNOWN"),
        lambda r: r[0]["cases"]["1"].update(forbidden_error_hits=["403"]),
        lambda r: r[0].update(expectation_supplied=False),
        lambda r: r[0].update(mode="diagnostic_explicit_profile"),
        lambda r: r[0].update(public_path_proof=False),
        lambda r: r[0].update(harness_sha256="0" * 64),
        lambda r: r[0].update(guardian_sha256="0" * 64),
        lambda r: r[0].update(provenance_sha256="0" * 64),
        lambda r: r[0].update(finished=None),
        lambda r: r[0].update(verdict="ACCEPTED"),
        lambda r: r[0].update(harness_warnings=["changed live state"]),
        lambda r: r[0]["published"].update(core_wheel_sha256="0" * 64),
        lambda r: r[0]["published"].update(profile_sha256="0" * 64),
        lambda r: r[0]["published"].update(artifacts={}),
        lambda r: r[0]["ci"].update(runner_os="Linux"),
        lambda r: r[0]["ci"].update(runner_arch="X64"),
        lambda r: r[0]["isolation"].update(live_untouched=False),
        lambda r: r[0]["isolation"].update(logs_containing_session_tokens=1),
        lambda r: r[0]["isolation"].update(live_after={"changed": True}),
        lambda r: r[0]["host"]["service_manager"].update(refused="active service"),
        lambda r: r[1].update(proof_root=r[0]["proof_root"]),
        lambda r: r[1]["lifecycle"].update(update_target={"version": "1.2.4"}),
    ],
    ids=[
        "missing-shard",
        "extra-shard",
        "duplicate-shard",
        "missing-case",
        "extra-case",
        "unknown-status",
        "forbidden-error",
        "unbound",
        "diagnostic",
        "public-identity",
        "harness",
        "guardian",
        "provenance",
        "unfinished",
        "false-verdict",
        "warnings",
        "core",
        "profile",
        "artifacts",
        "os",
        "arch",
        "live-changed",
        "token",
        "snapshots",
        "manager",
        "root",
        "target",
    ],
)
def test_corrupt_or_incomplete_evidence_is_never_assembled(mutation):
    values = receipts()
    mutation(values)
    with pytest.raises(ValueError, match=r".+"):
        reduce(values)


@pytest.mark.parametrize("candidate", [True, False])
@pytest.mark.parametrize(("state", "expected"), [("FAIL", "REJECTED"), ("REFUSED", "REFUSED")])
def test_a_real_case_failure_or_refusal_is_preserved_in_final_verdict(candidate, state, expected):
    values = receipts(candidate)
    values[0]["cases"]["1"]["status"] = state
    values[0]["verdict"] = "CANDIDATE_REJECTED" if candidate and state == "FAIL" else expected
    result = reduce(values)
    assert result["cases"]["1"]["status"] == state
    assert result["verdict"] == "STUDY_CASES_FAILED"
    assert result["combined_case_verdict"] == values[0]["verdict"]
    assert harness.exit_code(result["verdict"]) != 0


def test_duplicate_json_keys_cannot_hide_cases_or_provenance():
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        json.loads('{"cases": {}, "cases": {"1": {}}}', object_pairs_hook=reducer.unique_object)


@pytest.mark.parametrize(
    "extra",
    [
        ["--case", "1"],
        ["--bootstrap", "explicit"],
        ["--diagnostic-explicit-profile"],
        ["--candidate-wheel", "wheel.whl"],
    ],
)
def test_shards_cannot_omit_cases_or_run_diagnostics(extra):
    with pytest.raises(SystemExit):
        harness.parse_args(
            [
                "--expect-version",
                "1.2.3",
                "--expect-app-version",
                "1.2.3",
                "--shard",
                "public",
                "--accept-wheel-sha256",
                WHEEL,
                "--accept-profile-sha256",
                PROFILE,
                *extra,
            ]
        )


def test_shards_require_explicit_versions_and_pinned_hashes():
    for version, flags in [
        ("published", ["--accept-wheel-sha256", WHEEL, "--accept-profile-sha256", PROFILE]),
        ("1.2.3", []),
    ]:
        with pytest.raises(SystemExit):
            harness.parse_args(
                ["--expect-version", version, "--expect-app-version", version, "--shard", "public", *flags]
            )
    args = harness.parse_args(
        [
            "--expect-version",
            "1.2.3",
            "--expect-app-version",
            "1.2.3",
            "--shard",
            "public",
            "--accept-wheel-sha256",
            WHEEL,
            "--accept-profile-sha256",
            PROFILE,
        ]
    )
    assert args.case == list(harness.SHARDS["public"])


def test_study_is_manual_and_cannot_replace_or_publish_normal_release_receipts():
    workflow = yaml.safe_load((ROOT / ".github/workflows/acceptance-sharded.yml").read_text())
    events = workflow.get("on", workflow.get(True))
    assert set(events) == {"workflow_dispatch"}
    assert workflow["permissions"] == {}
    jobs = workflow["jobs"]
    matrix = jobs["shards"]["strategy"]["matrix"]
    assert matrix["os"] == ["macos-latest", "ubuntu-24.04"]
    assert set(matrix["shard"]) == set(harness.SHARDS)
    assert jobs["shards"]["strategy"]["fail-fast"] is False
    assert jobs["reduce"]["needs"] == "shards"
    assert jobs["reduce"]["if"] == "always()"
    assert jobs["reduce"]["strategy"]["matrix"]["runner_os"] == ["macOS", "Linux"]
    for job in jobs.values():
        assert job["permissions"] == {"contents": "read"}
        for step in job["steps"]:
            if "actions/upload-artifact@" in step.get("uses", ""):
                assert step["with"]["name"].startswith("acceptance-study-")


@pytest.mark.parametrize("defect", ["none", "missing", "wrong-name", "partial-only", "duplicate-json"])
def test_isolated_reducer_cli_uses_exact_artifact_inventory_and_never_overwrites(tmp_path, defect):
    values = receipts()
    for receipt in values:
        name = receipt["shard"]["name"]
        directory = tmp_path / "inputs" / f"acceptance-study-shard-macOS-{name}"
        directory.mkdir(parents=True)
        (directory / "result.json").write_text(json.dumps(receipt))
    public = tmp_path / "inputs" / "acceptance-study-shard-macOS-public" / "result.json"
    if defect == "missing":
        public.unlink()
    elif defect == "wrong-name":
        public.parent.rename(public.parent.with_name("different"))
    elif defect == "partial-only":
        public.parent.rename(public.parent.with_name("acceptance-study-partial-macOS-public"))
    elif defect == "duplicate-json":
        public.write_text('{"cases": {}, "cases": {}}')
    output = tmp_path / "result.json"
    command = [
        sys.executable,
        "-I",
        str(DIRECTORY / "reduce.py"),
        "--receipts",
        str(tmp_path / "inputs"),
        "--runner-os",
        "macOS",
        "--wheel-sha256",
        WHEEL,
        "--profile-sha256",
        PROFILE,
        "--output",
        str(output),
    ]
    ci_names = {
        "repository": "GITHUB_REPOSITORY",
        "sha": "GITHUB_SHA",
        "run_id": "GITHUB_RUN_ID",
        "run_attempt": "GITHUB_RUN_ATTEMPT",
        "workflow": "GITHUB_WORKFLOW",
        "event": "GITHUB_EVENT_NAME",
    }
    env = dict(os.environ) | {ci_names[key]: value for key, value in CI.items()}
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False, timeout=3)
    if defect != "none":
        assert result.returncode != 0
        assert not output.exists()
        return
    assert result.returncode == 0, result.stderr
    original = output.read_bytes()
    assert json.loads(original)["verdict"] == "STUDY_COMPLETE"
    second = subprocess.run(command, env=env, capture_output=True, text=True, check=False, timeout=3)
    assert second.returncode != 0
    assert output.read_bytes() == original


def test_study_summary_cannot_be_mistaken_for_production_acceptance():
    spec = importlib.util.spec_from_file_location("acceptance_summary", DIRECTORY / "summary.py")
    assert spec
    assert spec.loader
    summary = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(summary)
    text = summary.summarize(reduce(receipts()))
    assert "Study verdict: STUDY_COMPLETE" in text
    assert "Not eligible for release acceptance" in text
    assert "Combined case verdict (study evidence only)" in text
    assert "excludes workflow queue" in text
    assert "Preflight seconds" in text
    assert "**Verdict: ACCEPTED" not in text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("preflight_seconds", None),
        ("preflight_seconds", -1),
        ("preflight_seconds", float("inf")),
        ("preflight_seconds", True),
        ("finished", "2025-12-31T23:59:00+0000"),
        ("started", "2026-01-01T00:00:00"),
    ],
)
def test_missing_or_misleading_timing_cannot_be_assembled(field, value):
    values = receipts()
    values[0][field] = value
    with pytest.raises(ValueError, match=r"timing"):
        reduce(values)


def test_partial_progress_is_preserved_separately_and_never_downloaded_by_reducer():
    workflow = yaml.safe_load((ROOT / ".github/workflows/acceptance-sharded.yml").read_text())
    shards = workflow["jobs"]["shards"]
    assert shards["timeout-minutes"] == 20
    run = next(s for s in shards["steps"] if s.get("name") == "Run the acceptance harness")
    assert run["timeout-minutes"] == 15
    partial = next(
        s for s in shards["steps"] if s.get("with", {}).get("name", "").startswith("acceptance-study-partial-")
    )
    assert partial["if"] == "always()"
    assert partial["with"]["path"] == "/tmp/hz-acceptance-*/result.json"  # noqa: S108 - exact existing harness artifact path
    reducer_job = workflow["jobs"]["reduce"]
    assert reducer_job["timeout-minutes"] == 10
    download = next(s for s in reducer_job["steps"] if s.get("uses", "").startswith("actions/download-artifact@"))
    assert download["with"]["pattern"] == "acceptance-study-shard-${{ matrix.runner_os }}-*"


def test_wall_bounds_and_preflight_durations_describe_all_shards_not_the_first():
    values = receipts()
    values[0]["started"] = "2026-01-01T00:00:10+0000"
    values[0]["preflight_seconds"] = 10
    values[-1]["finished"] = "2026-01-01T00:02:00+0000"
    values[-1]["preflight_seconds"] = 20
    result = reduce(values)
    assert result["timing"]["harness_wall_span_seconds"] == 120
    assert result["timing"]["shards"]["public"]["harness_wall_seconds"] == 50
    assert result["timing"]["shards"]["public"]["preflight_seconds"] == 10
    assert result["timing"]["shards"]["browser"]["preflight_seconds"] == 20
    assert "preflight_seconds" not in result
