# ruff: noqa: INP001, PT009, PT027, SLF001 - standard library on the publisher runner
"""Exercise read-only synthetic test-merge convergence after the single ref write."""

import unittest
from unittest.mock import call, patch

import verify
import write

MERGE = "a" * 40
OLD = verify.MANIFEST["current_test_merge"]
ORIGINAL = verify.MANIFEST["current_pr_head"]


class TestMergeReadback(unittest.TestCase):
    """Keep publication bound to GitHub's coherent ordered merge."""

    def test_waits_for_exact_ref_and_rechecks_source(self) -> None:
        """Tolerate stale reads after the ref update without changing any ref."""
        old = {"merge_commit_sha": OLD, "head": {"sha": ORIGINAL}}
        new = {"merge_commit_sha": MERGE, "head": {"sha": verify.HEAD}}
        merge = {"parents": [{"sha": verify.BASE}, {"sha": verify.HEAD}], "tree": {"sha": verify.TREE}}
        with (
            patch.object(verify, "_merge_ref", side_effect=[None, OLD, MERGE]),
            patch.object(verify, "_refs") as refs,
            patch.object(verify, "_pull_request", side_effect=[old, new]),
            patch.object(verify, "_one", return_value=merge) as read,
            patch.object(verify.time, "sleep"),
        ):
            verify._test_merge(old, before_update=False, wait=True)
        self.assertEqual([call.args for call in refs.call_args_list], [(verify.HEAD,), (verify.HEAD,)])
        read.assert_called_once_with(f"git/commits/{MERGE}")

    def test_wrong_tree_fails_even_during_wait(self) -> None:
        """Reject a coherent SHA when its test merge tree is wrong."""
        pr = {"merge_commit_sha": MERGE, "head": {"sha": verify.HEAD}}
        merge = {"parents": [{"sha": verify.BASE}, {"sha": verify.HEAD}], "tree": {"sha": "other"}}
        with (
            patch.object(verify, "_merge_ref", return_value=MERGE),
            patch.object(verify, "_one", return_value=merge),
            self.assertRaisesRegex(SystemExit, "test merge tree drift"),
        ):
            verify._test_merge(pr, before_update=False, wait=True)

    def test_wait_timeout_and_normal_publisher_stop(self) -> None:
        """Require convergence within the post-update limit and immediately during publication."""
        pr = {"merge_commit_sha": None, "head": {"sha": verify.HEAD}}
        with (
            patch.object(verify, "_merge_ref", return_value=OLD),
            patch.object(verify.time, "monotonic", side_effect=[0, 100, 100]),
            patch.object(verify.time, "sleep") as sleep,
        ):
            for wait in (True, False):
                with self.assertRaisesRegex(SystemExit, "inspect the actual PR head and ref"):
                    verify._test_merge(pr, before_update=False, wait=wait)
        sleep.assert_not_called()

    def test_source_drift_stops_wait(self) -> None:
        """Treat changed protected refs as a hard failure."""
        pr = {"merge_commit_sha": None, "head": {"sha": verify.HEAD}}
        with (
            patch.object(verify, "_merge_ref", return_value=None),
            patch.object(verify, "_refs", side_effect=SystemExit("main drift")),
            patch.object(verify.time, "sleep"),
            self.assertRaisesRegex(SystemExit, "main drift"),
        ):
            verify._test_merge(pr, before_update=False, wait=True)

    def test_pr_snapshot_uses_rest_version_with_merge_sha(self) -> None:
        """Preserve merge_commit_sha while retaining the current step-log API."""
        with patch.object(verify.subprocess, "run") as api:
            api.return_value.stdout = b"[]"
            verify._api("pulls/683")
            verify._api("pulls/650")
            verify._api("actions/jobs/1/steps/2/logs", binary=True)
        versions = [item.args[0][3] for item in api.call_args_list]
        self.assertEqual(versions, ["X-GitHub-Api-Version: 2022-11-28"] * 2 + ["X-GitHub-Api-Version: 2026-03-10"])

    def test_accepted_update_is_never_replayed_after_readback_failure(self) -> None:
        """An inconclusive readback leaves the accepted ref update for inspection."""
        responses = [{"object": {"sha": sha}} for sha in (ORIGINAL, verify.BASE, verify.HEAD)]
        with (
            patch.object(write, "_api", side_effect=responses) as api,
            patch.object(write, "_verify", side_effect=[None, SystemExit("merge still pending")]) as gate,
            self.assertRaisesRegex(SystemExit, "merge still pending"),
        ):
            write._update("reviewed")
        self.assertEqual(
            gate.call_args_list,
            [call("reviewed", "--before-update"), call("reviewed", "--after-update", "--wait-test-merge")],
        )
        self.assertEqual(sum("PATCH" in item.args for item in api.call_args_list), 1)


if __name__ == "__main__":
    unittest.main()
