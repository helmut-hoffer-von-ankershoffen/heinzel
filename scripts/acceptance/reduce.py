"""Reduce four same-run hosted-VM receipts per OS, refusing incomplete or mixed evidence. Stdlib only."""

import argparse
import copy
import datetime
import math
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace


DIRECTORY = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("acceptance_harness", DIRECTORY / "run_acceptance.py")
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)
CI_KEYS = ("repository", "sha", "run_id", "run_attempt", "workflow", "event")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    """Duplicate JSON keys must never conceal a second case or a changed provenance field."""
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def reduce_receipts(receipts, runner_os, expected_ci, wheel_sha256, profile_sha256):
    """Require exactly-once complete coverage, unchanged preflight identities and same-attempt provenance."""
    require(runner_os in ("macOS", "Linux"), "Unknown target OS")
    require(len(receipts) == len(HARNESS.SHARDS), "Missing or extra shard receipts")
    require(
        all(expected_ci.get(key) for key in CI_KEYS),
        "Reducer lacks hosted CI provenance",
    )
    require(
        all(len(value) == 64 and set(value) <= set("0123456789abcdef") for value in (wheel_sha256, profile_sha256)),
        "Reducer lacks pinned release hashes",
    )
    expected_harness = HARNESS.file_sha256(DIRECTORY / "run_acceptance.py")
    combined = {}
    sources = {}
    roots = set()
    baseline = None
    args = None
    timing = {}
    for receipt in receipts:
        require(receipt.get("schema") == 2, "Unknown receipt schema")
        shard = receipt.get("shard", {})
        name = shard.get("name")
        require(name in HARNESS.SHARDS and name not in sources, "Unknown or duplicate shard")
        selected = HARNESS.SHARDS[name]
        require(
            shard.get("cases") == list(selected),
            "Shard declaration differs from its fixed partition",
        )
        require(
            set(receipt.get("cases", {})) == set(selected),
            "Missing or extra cases in shard",
        )
        ci = receipt.get("ci", {})
        require(
            all(ci.get(key) == expected_ci[key] for key in CI_KEYS),
            "Mixed CI source, run or attempt",
        )
        require(ci.get("runner_os") == runner_os, "Mixed runner OS")
        require(
            ci.get("runner_arch") == ("ARM64" if runner_os == "macOS" else "X64"),
            "Unexpected runner architecture",
        )
        require(
            receipt.get("harness_sha256") == expected_harness,
            "Unreviewed harness identity",
        )
        require(
            receipt.get("guardian_sha256") == HARNESS.GUARDIAN_SHA256,
            "Guardian identity differs",
        )
        require(
            receipt.get("provenance_sha256") == HARNESS.PROVENANCE_SHA256,
            "Provenance identity differs",
        )
        require(receipt.get("expectation_supplied") is True, "Unpinned expectations")
        require(
            receipt.get("mode") in ("acceptance_literal", "candidate_release"),
            "Diagnostic receipt",
        )
        candidate = receipt["mode"] == "candidate_release"
        require(
            receipt.get("public_path_proof") is (not candidate),
            "Contradictory public-path identity",
        )
        identity_key = "candidate_release" if candidate else "published"
        identity = receipt.get(identity_key, {})
        require(
            identity.get("core_wheel_sha256") == wheel_sha256,
            "Core differs from accepted hash",
        )
        require(
            identity.get("profile_sha256") == profile_sha256,
            "Profile differs from accepted hash",
        )
        require(receipt.get("started") and receipt.get("finished"), "Unfinished shard")
        started = datetime.datetime.fromisoformat(receipt["started"])
        finished = datetime.datetime.fromisoformat(receipt["finished"])
        require(started.utcoffset() is not None and finished.utcoffset() is not None, "Unzoned shard timing")
        require(finished >= started, "Reversed shard timing")
        preflight_seconds = receipt.get("preflight_seconds")
        require(
            type(preflight_seconds) in (int, float) and math.isfinite(preflight_seconds) and preflight_seconds >= 0,
            "Missing or invalid preflight timing",
        )
        timing[name] = {
            "started": receipt["started"],
            "finished": receipt["finished"],
            "preflight_seconds": preflight_seconds,
            "harness_wall_seconds": (finished - started).total_seconds(),
        }
        require(
            not receipt.get("preflight_failure") and not receipt.get("harness_warnings"),
            "Failed shard preflight or isolation",
        )
        isolation = receipt.get("isolation", {})
        require(isolation.get("live_untouched") is True, "Live identity was not preserved")
        require(
            isolation.get("logs_containing_session_tokens") == 0,
            "Session token reached a log",
        )
        require(isinstance(isolation.get("live_before"), dict), "Missing live snapshot")
        require(
            isolation.get("live_before") == isolation.get("live_after"),
            "Live snapshots differ",
        )
        require(
            not receipt.get("host", {}).get("service_manager", {}).get("refused"),
            "Service manager refused",
        )
        root = receipt.get("proof_root")
        require(
            isinstance(root, str) and root and root not in roots,
            "Reused or missing proof root",
        )
        roots.add(root)
        current_args = SimpleNamespace(
            candidate_wheel_url=candidate,
            diagnostic_explicit_profile=False,
            candidate_wheel=None,
            bootstrap="literal",
        )
        require(
            receipt.get("verdict") == HARNESS.verdict(current_args, receipt["cases"]),
            "Incorrect shard verdict",
        )
        for ident, case in receipt["cases"].items():
            require(ident not in combined, "Case executed more than once")
            require(case.get("name") == HARNESS.CASES[ident], "Case identity differs")
            require(
                case.get("status") in ("PASS", "FAIL", "REFUSED", "SKIPPED", "UNPROVABLE"),
                "Unknown case status",
            )
            require(
                case.get("status") != "PASS" or not case.get("forbidden_error_hits"),
                "Forbidden error in passing case",
            )
            combined[ident] = copy.deepcopy(case)
        if baseline is None:
            baseline, args = receipt, current_args
        else:
            for key in ("mode", "public_path_proof", identity_key, "limits"):
                require(
                    receipt.get(key) == baseline.get(key),
                    f"Mixed shard identity: {key}",
                )
            for key in ("bootstrap", "candidate", "update_target"):
                require(
                    receipt.get("lifecycle", {}).get(key) == baseline.get("lifecycle", {}).get(key),
                    f"Mixed lifecycle identity: {key}",
                )
        sources[name] = copy.deepcopy(receipt)
    require(set(combined) == set(HARNESS.CASES), "Incomplete full acceptance")
    combined_case_verdict = HARNESS.verdict(args, combined)
    earliest = min(datetime.datetime.fromisoformat(r["started"]) for r in receipts)
    latest = max(datetime.datetime.fromisoformat(r["finished"]) for r in receipts)
    return {
        "schema": "acceptance-sharding-study/v1",
        "mode": "acceptance_sharding_study",
        "public_path_proof": False,
        "release_eligible": False,
        "verdict": "STUDY_COMPLETE" if HARNESS.exit_code(combined_case_verdict) == 0 else "STUDY_CASES_FAILED",
        "combined_case_verdict": combined_case_verdict,
        "tested_mode": baseline["mode"],
        "release_identity": copy.deepcopy(baseline["candidate_release" if args.candidate_wheel_url else "published"]),
        "source_ci": {key: expected_ci[key] for key in CI_KEYS},
        "target_runner_os": runner_os,
        "cases": dict(sorted(combined.items(), key=lambda item: int(item[0]))),
        "lifecycle": copy.deepcopy(sources["update"]["lifecycle"]),
        "timing": {
            "scope": "Harness execution only; excludes workflow queue, runner setup, native prerequisites and upload",
            "earliest_harness_started": earliest.isoformat(),
            "latest_harness_finished": latest.isoformat(),
            "harness_wall_span_seconds": (latest - earliest).total_seconds(),
            "shards": timing,
        },
        "reviewed_sources": {
            "harness_sha256": expected_harness,
            "guardian_sha256": HARNESS.GUARDIAN_SHA256,
            "provenance_sha256": HARNESS.PROVENANCE_SHA256,
            "reducer_sha256": HARNESS.file_sha256(Path(__file__)),
        },
        "receipts": sources,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--runner-os", choices=("macOS", "Linux"), required=True)
    parser.add_argument("--wheel-sha256", required=True)
    parser.add_argument("--profile-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    files = sorted(args.receipts.glob("*/result.json"))
    expected_names = {f"acceptance-study-shard-{args.runner_os}-{name}" for name in HARNESS.SHARDS}
    require(
        {path.parent.name for path in files} == expected_names,
        "Unexpected artifact inventory",
    )
    receipts = []
    for path in files:
        receipt = json.loads(path.read_text(), object_pairs_hook=unique_object)
        require(
            path.parent.name == f"acceptance-study-shard-{args.runner_os}-{receipt.get('shard', {}).get('name')}",
            "Artifact name differs from shard identity",
        )
        receipts.append(receipt)
    result = reduce_receipts(
        receipts,
        args.runner_os,
        HARNESS.ci_record(),
        args.wheel_sha256,
        args.profile_sha256,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"verdict": result["verdict"], "result": str(args.output)}))
    return 0 if result["verdict"] == "STUDY_COMPLETE" else 1


if __name__ == "__main__":
    sys.exit(main())
