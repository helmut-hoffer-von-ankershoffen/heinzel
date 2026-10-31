"""Print a run's result.json as a short Markdown summary (for a job summary). Stdlib only; run with `python3 -I`."""

import json
import sys
from pathlib import Path


def summarize(result):
    """The verdict, what the run was, and one line per case."""
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


if __name__ == "__main__":
    sys.stdout.write(summarize(json.loads(Path(sys.argv[1]).read_text())))
