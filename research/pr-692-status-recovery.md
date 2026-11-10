# One-time status recovery for PR #692

The recovery uses the protected Sonar and coverage judges from `432006f27ce2df73a80f2f69a4e162378df2c862` and
publishes their new verdicts as commit statuses on the original PR head. The existing CI OK remains required. The
workflow leaves the original check runs, check suites and required publisher bindings in place.

- Independently review the exact recovery commit. Set `CORE_692_STATUS_RECOVERY_SHA` to that commit before the
  repository owner pushes it normally to `repair/checks-status-recovery`. The workflow accepts that ref, commit,
  owner and first attempt only. A push to the recovery ref avoids the legacy PR and main CI triggers.
  The standard pre-push selector treats workflow YAML as unmappable. Set `UNIT_TARGETS` to the recovery, script,
  and runner guard test files shown below. The normal hook still runs its workflow guards.
- The job checks the open PR, current base and head refs, test-merge parents and tree, and effective ruleset context
  sources before writing. It verifies CI run `42767677534` attempt 2 and its successful CI OK in suite
  `115826527542`. The original Coverage job and its archive `13739904749` come from attempt 1. The coverage adapter
  supplies only those exact inputs to the protected judge. The two source checkouts are the reviewed recovery
  commit and the pinned `main` commit.
- The workflow writes pending for `merge-gate` and `coverage-floor`, then records each genuine outcome. It rechecks
  the target and rules before each verdict and after publication. If publication or the final verification fails,
  it attempts to supersede this invocation's statuses with errors on the same head. The API offers no transaction
  across contexts. Any interruption or invalidation failure requires direct status inspection before queue use.
- After the hosted job, verify both status contexts on the exact head, the actual app/ruleset eligibility and the
  PR's protection decision. Successful status creation alone does not establish eligibility. Clear the activation
  variable after the one shot. Any merge requires its own reviewed path and exact-head admission, with the
  candidate-only full-suite policy preserved.

```bash
UNIT_TARGETS='tests/unit/recover_pr692_statuses_test.py tests/unit/scripts_test.py tests/unit/ci_macos_arm_test.py' \
  hz-gate git push -u origin repair/checks-status-recovery
```

[Incident PR](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/692) ·
[CI attempt 2](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/runs/42767677534/attempts/2) ·
[Commit statuses](https://docs.github.com/en/rest/commits/statuses) ·
[Required status checks](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)
