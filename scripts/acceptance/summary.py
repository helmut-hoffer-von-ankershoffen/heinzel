"""Print a run's result.json as a short Markdown summary (for a job summary). Stdlib only; run with `python3 -I`."""

import json
import sys
from pathlib import Path


RECEIPT = Path("reports/acceptance/result.json")


def summarize(result):
    """The verdict, what the run was, and one line per case."""
    if result.get("schema") == "acceptance-sharding-study/v1":
        return summarize_study(result)
    host = result.get("host", {})
    lines = [
        f"### Acceptance on {host.get('os') or 'macOS ' + str(host.get('macos', ''))} ({host.get('machine', '')})",
        "",
        f"**Verdict: {result.get('verdict')}** (mode `{result.get('mode')}`, public path proof: "
        f"{result.get('public_path_proof')}, expectation supplied: {result.get('expectation_supplied')})",
        "",
        "| Case | Status | Seconds | First problem |",
        "| --- | --- | --- | --- |",
    ]
    for ident, case in sorted(result.get("cases", {}).items(), key=lambda item: int(item[0])):
        problem = case.get("first_failure") or {}
        text = str(next(iter(problem.values()), "")) if len(problem) == 1 else str(problem.get("failure", ""))
        step = problem.get("step", "")
        detail = f"{step}: {text}" if step else text
        lines.append(f"| {ident} {case['name']} | {case['status']} | {case.get('seconds')} | {detail[:240]!r} |")
    if result.get("preflight_failure"):
        lines += ["", f"Preflight failure: {result['preflight_failure']}"]
    return "\n".join(lines) + "\n"


def summarize_study(result):
    """Keep a diagnostic reduction visibly distinct from release acceptance."""
    timing = result["timing"]
    lines = [
        f"### Acceptance sharding study on {result['target_runner_os']}",
        "",
        f"**Study verdict: {result['verdict']}**. Not eligible for release acceptance; public path proof: false.",
        f"Combined case verdict (study evidence only): `{result['combined_case_verdict']}`.",
        "",
        f"Harness wall span: {timing['harness_wall_span_seconds']}s. {timing['scope']}.",
        "",
        "| Shard | Harness start | Harness finish | Preflight seconds | Harness wall seconds |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, shard in sorted(timing["shards"].items()):
        lines.append(
            f"| {name} | {shard['started']} | {shard['finished']} | "
            f"{shard['preflight_seconds']} | {shard['harness_wall_seconds']} |"
        )
    lines += ["", "| Case | Status | Seconds |", "| --- | --- | --- |"]
    for ident, case in result["cases"].items():
        lines.append(f"| {ident} {case['name']} | {case['status']} | {case.get('seconds')} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) == 2 else RECEIPT
    sys.stdout.write(summarize(json.loads(path.read_text())))
