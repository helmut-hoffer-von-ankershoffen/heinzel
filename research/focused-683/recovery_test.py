# ruff: noqa: INP001, PT009, PT027, SLF001 - standard library on the bootstrap checkout
"""Check the fixed five-original plus two-recovery evidence composition."""

import unittest
from unittest.mock import call, patch

import verify

PLAN = verify.MANIFEST["recovery"]
ORIGINAL = PLAN["original_run"]
RECOVERY = ORIGINAL + 1
HELPER = "a" * 40
OWNER = verify.REPO.split("/")[0]


class RecoveryEvidenceTest(unittest.TestCase):
    """Reject changes to the original attempt and the two-job recovery scope."""

    def setUp(self) -> None:
        """Prepare both observed run shapes without contacting GitHub."""
        self.runs = {}
        self.jobs = {}
        self.artifacts = {}
        for run_id, recovered in ((ORIGINAL, False), (RECOVERY, True)):
            helper = HELPER if recovered else PLAN["original_helper"]
            workflow = "focused-683-recovery.yml" if recovered else "focused-683.yml"
            self.runs[run_id] = {
                "id": run_id,
                "event": "push",
                "head_branch": verify.RECOVERY_BRANCH if recovered else verify.BRANCH,
                "run_attempt": 1,
                "head_sha": helper,
                "path": ".github/workflows/" + workflow,
                "status": "completed",
                "conclusion": "success" if recovered else "failure",
                "workflow_id": PLAN["original_workflow"],
                "repository": {"id": verify.REPOSITORY_ID},
                "head_repository": {"id": verify.REPOSITORY_ID},
                "actor": {"login": OWNER},
                "triggering_actor": {"login": OWNER},
            }
            self.jobs[run_id] = []
            self.artifacts[run_id] = []
            for lane in PLAN["lanes"] if recovered else verify.MEMBERS:
                pin = PLAN["original_jobs"][lane]
                failed = not recovered and lane in PLAN["lanes"]
                self.jobs[run_id].append(
                    {
                        "id": pin["job"],
                        "name": f"Focused 683 {lane}",
                        "run_id": run_id,
                        "run_attempt": 1,
                        "head_sha": helper,
                        "status": "completed",
                        "runner_id": 9,
                        "conclusion": "failure" if failed else "success",
                    }
                )
                if not failed:
                    self.artifacts[run_id].append(
                        {
                            "id": pin.get("artifact", 12),
                            "digest": pin.get("digest", "fresh"),
                            "name": f"focused-683-{lane}-{run_id}-1",
                        }
                    )

    def verify_evidence(self) -> tuple[list, list]:
        """Run the composite orchestration with mocked GitHub transport."""
        with (
            patch.object(verify, "_one", side_effect=lambda path: self.runs[int(path.split("/")[2])]),
            patch.object(
                verify,
                "_inventory",
                side_effect=lambda path, key: (self.jobs if key == "jobs" else self.artifacts)[int(path.split("/")[2])],
            ),
            patch.object(verify, "_failed_setup") as failures,
            patch.object(verify, "_upload"),
            patch.object(verify, "_artifact") as archives,
        ):
            verify._evidence(RECOVERY, HELPER)
        return failures.call_args_list, archives.call_args_list

    def test_five_original_archives_plus_two_recovery_archives(self) -> None:
        """Use the authentic original passes and only the two repaired jobs."""
        failures, archives = self.verify_evidence()
        self.assertEqual(
            {item.args[0]["id"] for item in failures}, {PLAN["original_jobs"][lane]["job"] for lane in PLAN["lanes"]}
        )
        self.assertEqual(
            archives[-2:],
            [
                call(artifact, lane, RECOVERY, HELPER)
                for lane, artifact in zip(PLAN["lanes"], self.artifacts[RECOVERY], strict=True)
            ],
        )
        self.assertEqual(len(archives[:-2]), 5)
        self.assertTrue(all(item.args[2:] == (ORIGINAL, PLAN["original_helper"]) for item in archives[:-2]))

    def test_refuses_rewritten_original_or_retried_recovery(self) -> None:
        """The original stays failed and the recovery uses its actual first attempt."""
        self.runs[ORIGINAL]["conclusion"] = "success"
        with self.assertRaisesRegex(SystemExit, "unexpected evidence workflow"):
            self.verify_evidence()
        self.runs[ORIGINAL]["conclusion"] = "failure"
        self.runs[RECOVERY]["run_attempt"] = 2
        with self.assertRaisesRegex(SystemExit, "unexpected evidence workflow"):
            self.verify_evidence()

    def test_refuses_replaced_original_artifact_or_extra_recovery_job(self) -> None:
        """Prevent a same-name replacement or rerunning a passed lane."""
        artifact = self.artifacts[ORIGINAL][0]
        prior = artifact["digest"]
        artifact["digest"] = "replacement"
        with self.assertRaisesRegex(SystemExit, "original artifact changed"):
            self.verify_evidence()
        artifact["digest"] = prior
        self.jobs[RECOVERY].append({"name": "Focused 683 python"})
        with self.assertRaisesRegex(SystemExit, "unexpected jobs or artifacts"):
            self.verify_evidence()

    def test_original_failure_needs_the_actual_setup_diagnosis(self) -> None:
        """A failure from another tool cannot be substituted for the reviewed failure."""
        pin = PLAN["original_jobs"]["structural"]
        job = {
            "id": pin["job"],
            "steps": [
                {
                    "name": "Verify source and run evidence lane",
                    "status": "completed",
                    "conclusion": "failure",
                    "number": pin["failure_step"],
                },
                {"name": "Upload exact attempt evidence", "conclusion": "skipped"},
            ],
        }
        with (
            patch.object(verify, "_api", return_value=b"actionlint failed"),
            self.assertRaisesRegex(SystemExit, "no longer matches its reviewed setup diagnosis"),
        ):
            verify._failed_setup(job, pin)


if __name__ == "__main__":
    unittest.main()
