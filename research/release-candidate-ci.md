# Full release-candidate CI without a second ordinary PR run

Canonical release pull requests select the existing full CI workload in one run; a strict reader verifies that run's
source tree and complete original-attempt inventory before it can supply candidate evidence.

## Observed baseline and scope

The prior full candidate [42001988523](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/runs/42001988523)
was a successful first-attempt dispatch on `release/v0.9.16`, source
`f83493e0ba53f6769c557685eed2b8d1fb7265e1`. Its complete jobs API returned 63 of 63 jobs. It included four Linux
Python versions, eight ARM Mac Python/level cells, eight E2E cells, native, notifier, Electron and public consumers.
Workflow creation was `2026-11-05T09:23:34Z`; terminal update was `2026-11-05T09:51:09Z`: 27m35s including queue and
workflow execution. The same exact source head also had an ordinary PR run
[42001910329](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/runs/42001910329), created at `09:23:00Z`
and updated terminally at `09:34:14Z` (11m14s). The full dispatch began 34 seconds after the PR run: these runs overlapped.
Removing that duplicate therefore removes runner work and possible contention; it does not establish an 11-minute
serial wall-time saving. These are measured baselines, not evidence for the new protocol.

An ordinary PR previously selected Linux Python endpoints and Mac smoke rather than the full Python/native matrix.
Running ordinary PR CI and then dispatching full candidate CI repeated overlapping work. This change moves the full
workload into the canonical release PR run. It does not remove full main CI, its per-batch admission contract, security,
coverage, native checks, any case deadline or the required final aggregate. A release still needs every independently
required merge, publication, provenance and acceptance gate. No main validation or publication overlap is authorized
by candidate evidence alone.

## Two entry paths, one candidate

A normal `release.sh` PR must be in this repository, from this repository's `release/vX.Y.Z` branch into `main`, with
exact title `chore(release): vX.Y.Z`. Only canonical final versions select the release contract. Its existing PR event
runs the full matrix, including PR-specific dependency review and commit checks. Forks, arbitrary titles and unknown
selectors grant no candidate contract. Ordinary PR allocation remains unchanged.

Prepare release uses the GitHub token, whose PR creation does not generate a PR workflow event. Its existing explicit
CI dispatch remains the one full candidate run on the release branch. That dispatch keeps its existing event-specific
skips; the dependency audit and every full workload still run. The local release script gains no second dispatch.
Duplicate runs on one exact source head are refused as ambiguous rather than sampled until one passes.

## Exact source and complete execution

The read-only `Release candidate source` job records the real checkout commit/tree, release head and base, project
version, run ID/attempt, event and workflow/recorder hashes. The checked-out tree must equal the release head tree;
PR evidence also binds the synthetic merge parents to that exact base and head. A moved base or different tree fails,
without stripping a version, normalizing source or substituting another checkout. The artifact is created by CI, not
written by a release operator. Its success is a dependency of the genuine `CI OK` job.

The public reader requires explicit independently reviewed workflow and recorder fingerprints. It downloads the
unique artifact from the same official run, checks those source bytes at the recorded immutable commit, exhausts all
first-attempt job pages and re-fetches terminal run identity. Its reviewed inventory has 64 jobs: the previous full
workload plus the source-recording job. All four Linux Python versions, eight Mac Python/level cells and all eight E2E
lanes must actually succeed. Known conditional skips are explicit and event-specific. A failed, cancelled, rerun,
never-started, missing, extra or short-matrix workload cannot qualify; a newer unrelated green cannot replace it.
Inventory or source changes require a fresh review and fingerprint update. Other required checks still gate merging.

## Timing and qualification

The reader returns the exact run/event/head/tree and workflow creation/update bounds. These candidate bounds include
queueing; they exclude change preparation, earlier PR gates, publication, public acceptance and automatic rollout.
Record those phases separately, including failures and cold prerequisite downloads. Native package-mirror latency and
Mac runner queues remain independent bottlenecks. Do not claim twenty minutes by reporting only the fastest phase.

Before calling this live, observe both legitimate entry paths through normal required CI and compare the actual
complete inventory, tested tree, source artifact and clocks. Retain every first attempt. Three consecutive successful
controlled release trials are a working reliability measurement, not permission to waive any release gate. This source
preparation establishes no release-time improvement yet.
