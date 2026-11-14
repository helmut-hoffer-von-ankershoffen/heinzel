---
status: accepted
date: 2026-11-13
implementation: built
decision-makers: maintainer (decision), architect (shape)
related: 0097, 0114, 0134
---

# 0149: Merge evidence and release evidence are two tiers, and one selector picks the merge tier's tests

## Context and Problem Statement

Every pull request and every main push needed a reviewed scope manifest in a repository variable before a runner
started. The manifest pinned the exact tree, the base, every touched blob and twelve protected files. A moved main,
a fixup commit or a dependency bump voided it. Only the first attempt of a run counted, so a red main could not be
repaired by a rerun, and every main commit had its own concurrency group, which left unstartable queued runs. Five
places answered "what must run for this diff" with different maps, and the desktop app's own tests had no lane at
all: a change to its shutdown code merged on a Python-only selection.

That model is right for the artifact that gets published and too strict for the merges that come before it.

## Decision Drivers

- The release candidate keeps exact, first-attempt, full-matrix evidence: it is what users install.
- A merge must not wait on a human or agent step outside the repository before CI starts.
- Under-selection must be structurally hard, not prevented by review.
- A flaky job that passes on a rerun stays visible.

## Considered Options

1. Keep reviewed manifests for every merge.
2. Run the full matrix on every pull request.
3. Two tiers: an in-repo selector for merges that fails open to the full set, and the unchanged candidate model for
   releases.

## Decision Outcome

Option 3.

**Release tier** (the canonical `release/*` candidate): unchanged. Full matrix, first attempt, approved source pins,
whole-project coverage floor. `scripts/release_candidate.py` is not touched by this decision.

**Merge tier** (pull requests and main pushes):

- One selector, `scripts/affected.py`, maps the exact diff to focused lanes (`python`, `web`, `browser`, `desktop`,
  `consumers`). It fails open to the **full set**, the candidate's jobs, for workflows, scripts, the kernel,
  lockfiles (the desktop app's `package.json` and `pnpm-lock.yaml` included), unknown paths, a lane without a focused
  mapping and a Python selection over its limit. A Vitest or desktop selection over its limit runs that whole suite;
  a browser selection over its limit runs each whole browser e2e suite (`e2e-web-1`, `e2e-web-2`). Markdown outside
  the full-set paths runs the site tests.
- Every selected test file must run at least one case; the judge checks it against the lane's JUnit report.
- A change to the workflow, the code that decides and runs a workload, or what runs inside a full-set job
  (`PROTECTED_FILES`: `ci.yml`, the `Makefile`, the focused producers, `uv.lock`, `pyproject.toml`, the conftests,
  the desktop and web `package.json`, the CI helper scripts; `PROTECTED_PATTERNS`: the shared test helpers
  `tests/_*.py` and `tests/e2e/_*.py`) runs code the judge has not seen. The judge then requires a reviewed
  execution manifest that pins those tested blobs, as before this decision.
- A main run that took the full set is judged as the full set, even when a proof posted after it started would make
  the judge's own selection focused: full is a superset.
- The judge on the default branch (`scripts/focused_gate.py`) recomputes the selection with its own selector over the
  tested tree. A pull request cannot narrow what judges it: a change to the selector is itself a full-set path.
- A manifest in the plan registry is optional and can only widen a selection.
- Coverage is unchanged: 90 % over the whole of every touched production file. On the full set the Coverage job's
  whole-suite data supplies the same scoped gate.
- A rerun may complete the evidence. Every job that failed on an earlier attempt and passed later is written to the
  flake ledger, one line per job, so a rerun never hides a flake: for a pull request the merge gate uploads it, for
  a main push the main judge (`verify_main`) returns it in `passed_on_rerun`, and the coordinator's tooling files a
  flaky bead per line. Edge publishes from a main run that turned green on a rerun; edge is not the candidate.
- Main pushes share one concurrency group. A run compares against the last main commit whose CI OK passed **and**
  that the main judge accepted (commit status `hz/main-proof`, posted by the coordinator's tooling after
  `verify_main`), not the previous push. The tip's run so covers every push whose run was replaced or not accepted.
  With no accepted commit the run takes the full set. A main push of prose alone runs no app jobs, as before.
- Contract changes run every public plugin's checks serially against heinzel-plugins `main` in the `consumers`
  lane. It reports without blocking until the plugins' lock-by-contract toolkit release, then becomes required.

The dormant managed backend (`desktop/managed/`) has no lane and maps to the full set until it is deleted; its lint
steps leave CI with that deletion. `src/heinzel/modules/system/lifecycle/` and `_lifecycle_control.py` leave the
full-set list and are selected like other modules.

### Consequences

- Good: a pushed change starts CI with no manifest, and a fixup or a moved main costs one run.
- Good: the desktop app's Node unit suite runs on every desktop change.
- Good: a flaky main is repaired by a rerun, and the rerun is on record.
- Bad: a change the selector cannot narrow runs the full set, about 25 minutes instead of 3 to 14.
- Bad: the selector can under-select a path it maps. The fail-open rules, the meta-test that maps every tracked
  source directory to a lane or the full set, and the candidate's full matrix bound that risk.

### Confirmation

- `tests/unit/affected_test.py` maps every tracked source directory to a lane or the full set and names any path
  that has neither; it also pins the lane limits and whole-suite tokens to the resolver's.
- `tests/unit/focused_plan_test.py` covers the fail-open rules, widening-only manifests and the last-green main base.
- `tests/unit/focused_gate_test.py` covers the judge's own selection, a rerun that completes the evidence with its
  flake line, and the release tier's unchanged original-attempt rule.
- `tests/unit/ci_test.py` pins the main concurrency group, the full-set coverage step and the flake ledger upload.
