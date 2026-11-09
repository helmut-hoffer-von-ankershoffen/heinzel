# Required focused CI bootstrap

## Activation condition

The `main` ruleset requires `CI OK`, `merge-gate` and `coverage-floor` and allows squash merges without
requiring the latest base. The latter two checks are tied to the GitHub Actions app. PR workflow jobs and the
two checks written by `merge-gate.yml` are recorded against the PR head. `CI OK` for #692 is one observed
example. After `main` advances, a success recorded for the same PR head can still satisfy the ruleset if the
new test merge commit has no status. Checking the live base immediately before publishing a success still
leaves a window between check publication and merge.

Activate a focused required lane only once the merge mechanism rejects stale base evidence. In ruleset
`25762292`, the proposed change sets `strict_required_status_checks_policy` to `true` and keeps all three
required context names. The selector also requires the current base to be an ancestor of the tested head.
The local merge queue does not bind the base at GitHub's merge transaction. Until an enforceable route is
approved, keep the transition offline and leave affected merges held. [GitHub ruleset semantics][rules] and
[check selection][checks].

## Change manifest and receipts

Compare the complete NUL-delimited raw Git diff between the reviewed base and the exact head against an
allowlist on protected `main`. Record the old and new blob IDs and file modes for each path. Use no rename
detection so a rename must match its deletion and addition. Reject extra or absent paths, changed bytes or
modes, unknown event types, and any ambiguity in the base, head or computed merge tree. Unknown scope stays
ineligible for a focused verdict.

| Change | Manifest scope | Required test evidence |
| --- | --- | --- |
| Focused CI policy bootstrap | Exact diff bound by an independently reviewed detached manifest entry | Offline focused policy tests and negative cases. A policy change cannot approve itself. |
| #692 at `fe668ae0b2da4428665675ffabf79cafd055e4e4` | `tests/quarantine.toml` and `tests/unit/steps_signal_test.py` | Quarantine registry validation, Linux and ARM macOS process-signal tests, plus affected CLI E2E on both platforms. |
| Desktop download recovery at `73d3e8ec8857b23ea8b9fd4f80656e958dd8d008` | `src/heinzel/heads/_desktop_release.py` and `tests/unit/heads/desktop_release_progress_test.py` | Focused release-download tests, ARM macOS desktop-installation regression in hosted CI, and affected CLI installer E2E. |

The two example heads share base `432006f27ce2df73a80f2f69a4e162378df2c862`. These rows describe reviewed
surfaces for testing the policy. Revalidate the full diff and required job set against the actual protected
base after bootstrap. #692's first hosted run failed both CLI E2E jobs and `CI OK`; a passing subset of that
run supplies no focused success.

These are the exact raw diff records for the two fixtures. Each record shows the old and new modes, the old
and new blob IDs, and the status and path. Record the bootstrap change's own manifest independently after
its source is committed, so the file that records the approved blobs does not need to contain its own hash.

```text
:100644 100644 61ccaaa1f3389552f3afd100e4d41fcf81073f8c fdc0f9cf063b4af93aa785e154b290b3615ea714 M tests/quarantine.toml
:100644 100644 59cd7257e891cf282865df88eec0c4c541b77778 dbd154f2af1fd962cfb87442390a754e252e2559 M tests/unit/steps_signal_test.py
:100644 100644 933298d83001e251b3b82bd52bc950c83175c988 e5769256b7d1b38f551f226386592b466958e2c5 M src/heinzel/heads/_desktop_release.py
:100644 100644 b4b7efa8db6ffae79c5dfb64c847ba65d1c0c32b f90cb2a2d2489cafc664f9820d08b4027478adee M tests/unit/heads/desktop_release_progress_test.py
```

`scripts/focused_ci_transition.toml` contains offline fixtures. `merge_gate.py focused` can read an exact
attempt after a protected policy supplies `repository_id`, `workflow_id`, `workflow_path`, `workflow_sha256`
and `workload_app_id` alongside the selector digest. The checked-in policy leaves those pins unset.
Run the collector from protected source with the base, PR head and test merge Git objects available, without
checking out the PR. Pass `PR_NUMBER`, `PR_HEAD_SHA`, `FOCUSED_CI_RUN_ID` and `FOCUSED_CI_RUN_ATTEMPT`
from the trusted trigger. The GitHub token needs read access to actions, checks, contents and pull requests.

The collector compares the policy with protected `main`, checks the tested merge parents and tree, and
matches the workflow and selector bytes at the protected base and tested merge. It checks the workflow's
registered identity and the GitHub App IDs on the workload check suite and every job check. It reads all
pages of one attempt and preserves repeated job names. Missing, duplicate, skipped, cancelled, failing or
unexpected jobs fail validation. It rechecks the run and pull request after collecting the jobs. It prints
an evidence result and leaves required checks unpublished. The focused coverage route `coverage --focused`
fails closed pending an approved candidate coverage policy.

## Rollout

1. Verify the manifest and reducer offline. Review the exact detached bootstrap source and collector
   independently. Register and pin the exact workload workflow after reviewing its checkout of the GitHub
   test merge and its execution dependencies. Prove the protected collector against a real attempt before
   adding any required-check publishing path. The current workflow leaves all three gates unchanged.
2. Authorize strict freshness and a trusted publisher before enabling focused required checks. GitHub's
   Actions app ID identifies an app shared by workflows. It cannot distinguish a PR-controlled workflow
   from the protected reducer. A dedicated GitHub App publishing `CI OK` and configured as its expected
   integration can provide that separation. The trusted publisher must also validate the independent
   conventional commit, dependency review, secret scan, security audit and Sonar evidence before success.
   Keep the three context names and preserve protected, independent gate evaluation.
3. Resolve the required coverage timing before activation. Today `coverage-floor` judges a `Coverage` job
   that runs `make test` on a PR. A focused receipt cannot satisfy it. Keep the check required and hold
   focused promotion while it lacks valid evidence. Moving that requirement to the final merged candidate
   needs a separate policy decision and a candidate-side check that enforces the same floor on its own
   successful `Coverage` job and `coverage-total` artifact. The full suite and full coverage belong on the
   final merged candidate. Main's badge and the focused subset supply neither measurement.
4. After the authorized migration, activate only the independently reviewed manifest entries. Re-pin
   `APPROVED_SOURCES` after independent review whenever executed `ci.yml` or the candidate recorder changes.
   Use the exact merged digests and update the release-policy fixture in the same re-pin review.

| Transition | Expected result |
| --- | --- |
| Today, loose freshness | Offline validation can reject an old receipt. GitHub can still accept a prior success on the unchanged head. Hold activation. |
| Proposed activation | Publish focused success only from the trusted integration, against current reviewed identities and real job receipts. Advancing `main` then blocks the old head under strict freshness. |
| Rollback | Hold merges, disable focused eligibility, and revoke outstanding focused check successes before resuming. Restore the previous required-check publisher only after confirming focused successes from it cannot satisfy the ruleset. Keep strict freshness and the candidate's suite and coverage. |

[rules]: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets#require-status-checks-to-pass-before-merging
[checks]: https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks#conflicts-between-head-commit-and-test-merge-commit
