# ruff: noqa: INP001, PT009, PT027 - standard library on the publisher runner
"""Exercise the fail-closed PR coverage evidence boundary."""

import copy
import json
import unittest

import coverage_evidence as policy


class CoverageEvidenceTest(unittest.TestCase):
    """Measure thresholds, source opportunities and evidence binding."""

    def setUp(self) -> None:
        """Build separate fixtures with one missed statement and one missed branch."""
        self.python = "src/heinzel/example.py"
        self.web = "web/src/example.ts"
        self.web_key = "/checkout/" + self.web
        self.shape = {
            "statements": list(range(1, 19)),
            "branches": [[3, 4], [3, 5]],
        }
        self.web_report = {
            "path": self.web_key,
            "s": {str(index): 4 for index in range(8)},
            "statementMap": {str(index): {"start": {"line": index + 1}} for index in range(8)},
            "b": {"0": [2, 0]},
            "branchMap": {"0": {"locations": [{}, {}]}},
        }
        self.manifest = {
            "changed_paths": {self.python: "A", self.web: "M"},
            "coverage": {
                "type": policy.EVIDENCE_TYPE,
                "floor_percent": 80,
                "threshold_percent": 90,
                "python_files": [self.python],
                "web_files": [self.web],
                "source_shape_sha256": {
                    "python": {self.python: policy.source_digest(self.shape)},
                    "web": {
                        self.web: policy.source_digest(
                            {name: self.web_report[name] for name in ("statementMap", "branchMap")}
                        )
                    },
                },
            },
        }
        self.identity = {"base": "base", "head": "head", "lane": "python", "run": "42", "attempt": "1"}
        self.summary = {"covered_lines": 17, "num_statements": 18, "covered_branches": 1, "num_branches": 2}
        self.report = {
            "executed_lines": list(range(1, 18)),
            "missing_lines": [18],
            "executed_branches": [[3, 4]],
            "missing_branches": [[3, 5]],
            "summary": self.summary,
        }
        web_summary = {
            "lines": {"covered": 8, "total": 8},
            "statements": {"covered": 8, "total": 8},
            "branches": {"covered": 1, "total": 2},
        }
        self.data = {
            "python-coverage.json": {"meta": {"branch_coverage": True}, "files": {self.python: self.report}},
            "python-opportunities.json": {self.python: self.shape},
            "web-coverage/coverage-final.json": {self.web_key: self.web_report},
            "web-coverage/coverage-summary.json": {self.web_key: web_summary, "total": web_summary},
        }

    def read(self, name: str) -> bytes:
        """Return an encoded artifact member."""
        return json.dumps(self.data[name]).encode()

    def test_focused_threshold_is_unrounded_and_above_floor(self) -> None:
        """Accept exactly 90% and reject 85% even though the old floor passes."""
        evidence = policy.evidence(self.manifest, self.identity, self.read)
        self.assertEqual(evidence["aggregate_statements_and_branches"], (18, 20))
        self.report["executed_lines"].remove(17)
        self.report["missing_lines"].append(17)
        self.summary["covered_lines"] = 16
        with self.assertRaisesRegex(SystemExit, "below 90%"):
            policy.evidence(self.manifest, self.identity, self.read)

    def test_zero_execution_keeps_all_source_opportunities(self) -> None:
        """An unimported file supplies zero hits across the full denominator."""
        self.report.update(
            executed_lines=[],
            missing_lines=self.shape["statements"],
            executed_branches=[],
            missing_branches=self.shape["branches"],
        )
        self.summary.update(covered_lines=0, covered_branches=0)
        with self.assertRaisesRegex(SystemExit, "0/20 below 80%"):
            policy.evidence(self.manifest, self.identity, self.read)

    def test_changed_file_cannot_be_left_out(self) -> None:
        """Reject an affected production list missing a changed module."""
        self.manifest["changed_paths"]["src/heinzel/new.py"] = "A"
        with self.assertRaisesRegex(SystemExit, "incomplete python affected production scope"):
            policy.evidence(self.manifest, self.identity, self.read)

    def test_python_cannot_drop_an_uncovered_branch(self) -> None:
        """Reject missing opportunities even if summary totals are recomputed."""
        self.report["missing_branches"] = []
        self.summary["num_branches"] = 1
        with self.assertRaisesRegex(SystemExit, "incomplete or overlapping"):
            policy.evidence(self.manifest, self.identity, self.read)
        self.shape["branches"] = [[3, 4]]
        with self.assertRaisesRegex(SystemExit, "unexpected Python source opportunities"):
            policy.evidence(self.manifest, self.identity, self.read)

    def test_web_cannot_drop_an_uncovered_alternative(self) -> None:
        """Require one nonnegative integer count per mapped alternative."""
        self.identity["lane"] = "web"
        evidence = policy.evidence(self.manifest, self.identity, self.read)
        self.assertEqual(evidence["aggregate_statements_and_branches"], (9, 10))
        self.web_report["b"]["0"] = [2]
        with self.assertRaisesRegex(SystemExit, "incomplete Vitest source map"):
            policy.evidence(self.manifest, self.identity, self.read)
        self.web_report["b"]["0"] = [2, True]
        with self.assertRaisesRegex(SystemExit, "invalid Vitest hit counters"):
            policy.evidence(self.manifest, self.identity, self.read)

    def test_publisher_rejects_stale_or_other_evidence_type(self) -> None:
        """Keep the PR-scoped artifact tied to its exact source, attempt and measurements."""
        evidence = policy.evidence(self.manifest, self.identity, self.read)
        self.data["pr-coverage.json"] = copy.deepcopy(evidence)
        policy.verify(self.manifest, self.identity, self.read)
        self.data["pr-coverage.json"]["type"] = "whole-project-coverage/v1"
        with self.assertRaisesRegex(SystemExit, "typed PR coverage evidence mismatch"):
            policy.verify(self.manifest, self.identity, self.read)
        self.data["pr-coverage.json"] = evidence
        with self.assertRaisesRegex(SystemExit, "typed PR coverage evidence mismatch"):
            policy.verify(self.manifest, self.identity | {"head": "other head"}, self.read)


if __name__ == "__main__":
    unittest.main()
