# ruff: noqa: INP001, T201
"""Validate whole-file affected production measurements for the #683 admission."""

import hashlib
import json
from collections.abc import Callable, Iterable
from typing import Any

EVIDENCE_TYPE = "pr-affected-production-coverage/v1"
COVERAGE_FLOOR = 80
FOCUSED_THRESHOLD = 90
BRANCH_ENDPOINTS = 2
PYTHON_RAW_FILES = ("python-coverage.json", "python-opportunities.json")
WEB_RAW_FILES = ("web-coverage/coverage-final.json", "web-coverage/coverage-summary.json")


def require(condition: bool, message: str) -> None:
    """Require a coverage evidence contract.

    Raises:
        SystemExit: If the contract fails.
    """
    if not condition:
        raise SystemExit(message)


def scope(manifest: dict[str, Any]) -> dict[str, list[str]]:
    """Require the reviewed policy and every changed production file."""
    coverage = manifest["coverage"]
    require(
        coverage["type"] == EVIDENCE_TYPE
        and coverage["floor_percent"] == COVERAGE_FLOOR
        and coverage["threshold_percent"] == FOCUSED_THRESHOLD,
        "unexpected PR coverage policy",
    )
    files = {"python": coverage["python_files"], "web": coverage["web_files"]}
    changed = {
        "python": {path for path in manifest["changed_paths"] if path.startswith("src/heinzel/")},
        "web": {
            path for path in manifest["changed_paths"] if path.startswith("web/src/") and not path.endswith(".test.ts")
        },
    }
    for lane, paths in files.items():
        require(
            bool(changed[lane])
            and len(paths) == len(set(paths))
            and changed[lane] <= set(paths)
            and set(paths) == set(coverage["source_shape_sha256"][lane]),
            f"incomplete {lane} affected production scope",
        )
    return files


def _threshold(covered: int, total: int, label: str, minimum: int) -> None:
    """Compare exact opportunity counts without rounding."""
    require(
        total > 0 and 0 <= covered <= total and 100 * covered >= minimum * total,
        f"{label}: {covered}/{total} below {minimum}% or invalid",
    )


def _lines(values: list[int]) -> set[int]:
    require(all(type(line) is int and line > 0 for line in values), "invalid source line")
    require(len(values) == len(set(values)), "duplicate source line")
    return set(values)


def _branches(values: list[list[int]]) -> set[tuple[int, ...]]:
    require(
        all(len(arc) == BRANCH_ENDPOINTS and all(type(line) is int and line != 0 for line in arc) for arc in values),
        "invalid branch arc",
    )
    arcs = {tuple(arc) for arc in values}
    require(len(values) == len(arcs), "duplicate branch arc")
    return arcs


def _python(
    data: dict[str, Any], inventory: dict[str, Any], shapes: dict[str, str]
) -> dict[str, dict[str, tuple[int, int]]]:
    files = data["files"]
    require(data["meta"]["branch_coverage"] is True and set(files) == set(inventory) == set(shapes), "Python scope")
    metrics = {}
    for path, digest in shapes.items():
        report = files[path]
        require(source_digest(inventory[path]) == digest, f"unexpected Python source opportunities: {path}")
        lines, missing = _lines(report["executed_lines"]), _lines(report["missing_lines"])
        branches = _branches(report["executed_branches"])
        missing_branches = _branches(report["missing_branches"])
        require(
            not lines & missing
            and not branches & missing_branches
            and lines | missing == _lines(inventory[path]["statements"])
            and branches | missing_branches == _branches(inventory[path]["branches"]),
            f"incomplete or overlapping Python opportunities: {path}",
        )
        counts = {
            "statements": (len(lines), len(lines) + len(missing)),
            "branches": (len(branches), len(branches) + len(missing_branches)),
        }
        summary = report["summary"]
        expected = {
            "covered_lines": counts["statements"][0],
            "num_statements": counts["statements"][1],
            "covered_branches": counts["branches"][0],
            "num_branches": counts["branches"][1],
        }
        require(
            all(type(summary[key]) is int and summary[key] == value for key, value in expected.items()),
            f"Python summary differs from measured locations: {path}",
        )
        metrics[path] = counts
    return metrics


def _web_file(report: dict[str, Any]) -> dict[str, tuple[int, int]]:
    statements = report["s"]
    branches = report["b"]
    statement_map = report["statementMap"]
    branch_map = report["branchMap"]
    require(
        set(statements) == set(statement_map)
        and set(branches) == set(branch_map)
        and all(len(hits) == len(branch_map[node]["locations"]) > 0 for node, hits in branches.items()),
        "incomplete Vitest source map",
    )
    branch_hits = [hits for alternatives in branches.values() for hits in alternatives]
    require(
        all(type(hits) is int and hits >= 0 for hits in (*statements.values(), *branch_hits)),
        "invalid Vitest hit counters",
    )
    statement_lines = {statement_map[node]["start"]["line"] for node in statements}
    covered_lines = {statement_map[node]["start"]["line"] for node, hits in statements.items() if hits > 0}
    return {
        "lines": (len(covered_lines), len(statement_lines)),
        "statements": (sum(hits > 0 for hits in statements.values()), len(statements)),
        "branches": (sum(hits > 0 for hits in branch_hits), len(branch_hits)),
    }


def _web(raw: dict[str, bytes], shapes: dict[str, str]) -> dict[str, dict[str, tuple[int, int]]]:
    reports = json.loads(raw["web-coverage/coverage-final.json"])
    summaries = json.loads(raw["web-coverage/coverage-summary.json"])
    require(len(reports) == len(shapes) and set(summaries) == {*reports, "total"}, "unexpected Vitest scope")
    metrics = {}
    for path, digest in shapes.items():
        keys = [key for key in reports if key.endswith("/" + path) and reports[key]["path"] == key]
        require(len(keys) == 1, f"missing or duplicate Vitest source map: {path}")
        key = keys[0]
        shape = {name: reports[key][name] for name in ("statementMap", "branchMap")}
        require(source_digest(shape) == digest, f"unexpected Vitest source opportunities: {path}")
        metrics[path] = _web_file(reports[key])
        _web_summary(summaries[key], metrics[path])
    _web_summary(
        summaries["total"],
        {metric: _sum(counts[metric] for counts in metrics.values()) for metric in ("lines", "statements", "branches")},
    )
    return metrics


def _web_summary(summary: dict[str, Any], metrics: dict[str, tuple[int, int]]) -> None:
    for metric, counts in metrics.items():
        reported = summary[metric]["covered"], summary[metric]["total"]
        require(
            all(type(count) is int for count in reported) and reported == counts,
            f"Vitest {metric} summary differs from source map",
        )


def _sum(counters: Iterable[tuple[int, int]]) -> tuple[int, int]:
    covered = total = 0
    for count, opportunities in counters:
        covered += count
        total += opportunities
    return covered, total


def source_digest(shape: dict[str, Any]) -> str:
    """Hash only source locations, independent of coverage hits."""
    return hashlib.sha256(json.dumps(shape, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def evidence(manifest: dict[str, Any], identity: dict[str, Any], read: Callable[[str], bytes]) -> dict[str, Any]:
    """Recompute typed coverage evidence from reviewed scope and raw measurements."""
    require(identity["lane"] == "python", "PR coverage evidence requires the Python lane")
    scope(manifest)
    shapes = manifest["coverage"]["source_shape_sha256"]["python"]
    raw = {name: read(name) for name in PYTHON_RAW_FILES}
    data = json.loads(raw["python-coverage.json"])
    metrics = _python(data, json.loads(raw["python-opportunities.json"]), shapes)
    statements = _sum(counts["statements"] for counts in metrics.values())
    branches = _sum(counts["branches"] for counts in metrics.values())
    totals = data["totals"]
    expected = dict(
        zip(
            ("covered_lines", "num_statements", "covered_branches", "num_branches"),
            (*statements, *branches),
            strict=True,
        )
    )
    require(
        all(type(totals[key]) is int and totals[key] == count for key, count in expected.items()),
        "Coverage.py TOTAL differs from affected production opportunity counts",
    )
    for path, counts in metrics.items():
        covered, total = _sum((counts["statements"], counts["branches"]))
        print(f"{path}: {covered}/{total} statements and branches")
    covered, total = _sum((statements, branches))
    label = "Affected Python coverage.py TOTAL"
    _threshold(covered, total, label, COVERAGE_FLOOR)
    _threshold(covered, total, label, FOCUSED_THRESHOLD)
    print(f"{label}: {covered}/{total} ({100 * covered / total:.2f}%)")
    return {
        "type": EVIDENCE_TYPE,
        "identity": identity,
        "floor_percent": COVERAGE_FLOOR,
        "threshold_percent": FOCUSED_THRESHOLD,
        "files": metrics,
        "aggregate_statements_and_branches": (covered, total),
        "raw_sha256": {name: hashlib.sha256(blob).hexdigest() for name, blob in raw.items()},
    }


def web_diagnostics(manifest: dict[str, Any], read: Callable[[str], bytes]) -> dict[str, dict[str, tuple[int, int]]]:
    """Read optional web coverage counters separately from admission."""
    scope(manifest)
    shapes = manifest["coverage"]["source_shape_sha256"]["web"]
    return _web({name: read(name) for name in WEB_RAW_FILES}, shapes)


def verify(manifest: dict[str, Any], identity: dict[str, Any], read: Callable[[str], bytes]) -> None:
    """Require the recorded coverage type, identity, counts and raw hashes to match."""
    recomputed = evidence(manifest, identity, read)
    recorded = json.loads(read("pr-coverage.json"))
    require(
        json.dumps(recorded, sort_keys=True) == json.dumps(recomputed, sort_keys=True),
        "typed PR coverage evidence mismatch",
    )
