# Required focused CI bootstrap

## Activation condition

The `main` ruleset requires `CI OK`, `merge-gate` and `coverage-floor` and allows squash merges without
requiring the latest base. The latter two checks are tied to the GitHub Actions app. PR workflow jobs and the
two checks written by `merge-gate.yml` are recorded against the PR head. `CI OK` for #692 is one observed
example. After `main` advances, a success recorded for the same PR head can still satisfy the ruleset if the
new test merge commit has no status. Checking the live base immediately before publishing a success still
leaves a window between check publication and merge.

Activate a reduced required lane only once the merge mechanism rejects stale base evidence. One route is
strict required status checks, plus selector admission requiring the current base to be an ancestor of the
tested head. A required merge queue could instead judge the actual merge-group commit. The current local
merge queue does not bind the base at GitHub's merge transaction. Until an enforceable route is chosen, run
the existing required workloads. [GitHub ruleset semantics][rules] and [check selection][checks].

## Change manifest and receipts

Compare the complete NUL-delimited raw Git diff between the reviewed base and the exact head against an
allowlist on protected `main`. Record the old and new blob IDs and file modes for each path. Use no rename
detection so a rename must match its deletion and addition. Reject extra or absent paths, changed bytes or
modes, unknown event types, and any ambiguity in the base, head or computed merge tree. Changed source that
is outside an allowed entry uses ordinary CI.

| Change | Manifest scope | Required test evidence |
| --- | --- | --- |
| Focused CI policy bootstrap | Exact reviewed file and blob list, including workflow, selector, manifest, and tests | Ordinary CI, with an additional offline policy suite. A policy change cannot admit itself to reduced CI. |
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

The trusted reducer runs from the protected default branch. Its receipt binds repository, PR number, base,
head, tested merge commit and tree, selector and workflow source identities, run ID and attempt, and the
exact expected job identities. Derive the expected list from trusted source and obtain each job result from
GitHub for that run attempt. A missing, duplicate, skipped, cancelled, failing, or mismatched expected job
blocks the focused verdict. Keep receipt production separate from PR-controlled code. A diagnostic dispatch
never satisfies a required check. The reducer checks the protected base at issuance as a second layer.

## Rollout

1. Land the selector and receipt verifier in shadow mode through an ordinary, independently reviewed PR.
   Its full existing required jobs remain the merge evidence. Exercise changed paths, blobs and modes,
   selectors, base and head changes, duplicate and skipped jobs, and missing or cross-attempt receipts
   offline before observing real shadow receipts.
2. Independently review and re-pin `APPROVED_SOURCES` whenever the executed CI workflow or candidate
   recorder changes. Use the exact merged source digests and update the corresponding release-policy
   fixture in the same re-pin review.
3. After base freshness is enforced at merge, enable only the reviewed manifest entries. Retain conventional
   commits, dependency review, secret scan, security audit, Sonar, and exact-run full coverage for focused PRs.
   Retain the same coverage threshold and require the successful `Coverage` job and its `coverage-total`
   artifact from the selected run. Keep the full suite and coverage for merged candidates. A main badge,
   focused subset, or pre-bootstrap run supplies no full coverage for a focused PR.

[rules]: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets#require-status-checks-to-pass-before-merging
[checks]: https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks#conflicts-between-head-commit-and-test-merge-commit
