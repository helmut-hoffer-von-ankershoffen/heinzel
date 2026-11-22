# AGENTS.md — CI/CD

- Pin actions to full SHAs with the tag as a comment (`gh api repos/<o>/<r>/commits/<tag> --jq .sha`).
- Set `permissions: {}` at the top and least privilege per job; set `persist-credentials: false`.
- Pass untrusted `${{ }}` into `run:` only via `env:`.
- Run scripts through an interpreter (`bash scripts/x.sh`, `uv run --script scripts/x.py`), never by path: plugins get
  them without the executable bit.
- `make lint` runs actionlint and zizmor.
- Workflows shared with plugins are symlinked into `template/`; the template's `ci.yml` is generated from ours
  (`make fix`).

macOS jobs use Apple-silicon ARM64 hosted runners only (`macos-latest`). Never add Intel macOS runner labels,
Intel large runners, or Intel self-hosted runners. Keep the shared bootstrap architecture check and the native
`make ci-macos-arm` check so an unexpected architecture fails closed. Preserve the full ARM Python-version,
native, browser, Electron and release-acceptance coverage and its limits. Linux coverage is unchanged.

macOS e2e, Electron and setup lanes run on push to main, schedule and dispatch, and on a release PR
(`full_release`); an ordinary PR runs every Linux job plus macOS unit, integration, macOS-specific and notifier.
Narrow by matrix, never by a job-level `if` on a required job. The nightly requires the full matrix (`CI OK`
checks it), and a red nightly or main macOS lane blocks the next release. PR matrices use `fail-fast: false`, and the
template job starts with the Linux jobs instead of waiting for lint. The pre-push hook is capped at lint, types and
related tests (no `-x`, every failure in one pass, target 120 s, never the whole unit suite).

Constraints per workflow:

- `ci.yml`: ordinary PRs, main pushes and manual runs execute the full test, native, browser, build and
  coverage matrix without an external admission manifest. Schedules retain bounded maintenance. `CI OK` requires
  every selected owning job to succeed; skipped required jobs fail. Coverage runs read-only and enforces 90 percent.
  Candidate identity and source recording remain separate from ordinary full CI. Forks remain read-only.
- Merge policy: require `CI OK` from GitHub Actions and `SonarCloud Code Analysis` from the Sonar integration.
  Independently review the exact head before merging, especially workflow and gate changes. This uses conventional
  reviewed-PR trust: PR jobs execute the reviewed repository code; job names alone do not authenticate that code.
  The old custom `merge-gate` and `coverage-floor` checks remain diagnostic during migration and are not admission
  requirements. Their registry and project-wide Sonar dependency must not prevent corrective PRs from merging.
- `update-prs.yml`: core keeps tested PR heads for the merge queue; plugin repositories update behind branches and
  approve the resulting CI runs. Closed code-scanning threads are resolved in both.
- `acceptance.yml`: `contents: read`, no secrets, publishes nothing; uploads `result.json`, which promotion reads. A
  candidate run (release wheel URL + sha256 + profile URL) is labelled `CANDIDATE_*`, never a public-path pass.
- `prepare-release.yml` (`make release`): opens the release PR `chore(release): vX.Y.Z` and dispatches CI on it (token-made
  PRs trigger none). A maintainer reviews all checks before merging. The owner decisions of 2026-11-22 in the root
  AGENTS.md allow replacing this review and the macOS PR lanes once #907 and #933 merge.
- `edge.yml`: dark until `vars.RELEASE_CHANNELS` is set (ADR 0114); then publishes pre-releases `edge-v<version>`,
  never latest, never on PyPI.
- `pypi.yml`: manual dispatch on a final version tag; verifies and promotes the exact signed official GitHub release
  wheel and sdist without rebuilding, uploads only core with isolated trusted publishing in environment `pypi`.
- `release.yml`: manual dispatch for one optional plugin; never builds or tests core.
- `source-review.yml`: dispatch on the protected ref `trusted/source-review` only (`make review PR=<n>`); paid, tool-free
  review of one PR's source; fails closed unless live head, base and protected ref equal the dispatch; core only.
- `focused-job.yml`: core only; manual dispatch of one e2e lane or test ids, pytest ids or vitest files on one hosted
  runner, testing only the dispatched branch (input `ref` must equal it); `os`, `kind` and `selector` are validated by
  `scripts/focused_job.py` and never pass through a shell. `contents: read`, no repository secrets (the read-only job
  token only, as in ci.yml's e2e), never writes the cache, 20 min; a diagnostic that gates nothing.
- `fuzz.yml`: core only. `claude-review.yml`: the "Gate review" check on every PR; passes without a model call unless gate paths change, then an Opus review must end in `VERDICT: APPROVE` (needs `vars.CLAUDE_REVIEW`; bots, forks and drafts fail with "needs a human or agent review").
- `docs.yml`: deploys to GitHub Pages when the repository is public or `vars.DOCS_PAGES` is `true`, else keeps an
  artifact (ADR 0082).


### Legacy focused plans (diagnostic compatibility only)

`FOCUSED_CI_PLANS` is the administrator-controlled, size-bounded JSON registry. Publish an entry only after
independent source and scope review. Use `<event>:<head>` for a known head. Before a normal main squash or batch
has a commit SHA, use `push-tree:<reviewed-result-tree>` and omit `head`. This key applies only to main push and
still binds the exact reviewed aggregate base, tree and diff. The resulting evidence records the real head SHA.
An entry has these fields.

- `schema` is 1. `event`, `ref`, `base`, `head` and `tree` bind the exact workload. Before opening a PR, use
  `refs/pull/*/merge` as its planning ref and compute the expected tree with `git merge-tree --write-tree BASE HEAD`.
  CI independently binds the real PR number and test merge with ordered parents `[base, head]`. Evidence records
  the actual tested merge commit. Provide an optional exact `commit` only when the GitHub merge SHA already exists.
  GitHub can report older PR and run base SHAs. Each reported SHA must be in the tested base's Git history. The
  protected judge requires the tested base to equal current `main` and rechecks `main` and the merge ref before
  its verdict.
- `diff_sha256` is the SHA-256 of `git diff-tree --no-commit-id -r --raw --no-abbrev --no-renames -z BASE TREE`.
  `changed` is its complete path-to-status map. Rename halves are reviewed as deletion and addition.
- `production` maps every changed and explicitly affected covered Python file to its Git blob ID in that tree.
  `mode` is `pr-scoped` when that map is nonempty, or `python-coverage-not-applicable` for a separately
  reviewed scope with no applicable Python coverage. `config_blob` is the tested `pyproject.toml` Git blob.
- `execution` maps the producer inputs in `scripts/focused_plan.py` to exact tested Git blobs. `selection` maps the
  supported lane names to owning test files or exact node IDs. `case_sha256` maps each lane to the digest of the
  compact sorted JSON array of its expected JUnit `[classname, name]` pairs. Review affected integration and
  native boundaries separately from the Python coverage denominator.

Run `python3 -I scripts/focused_plan.py --suggest BASE` in the checkout to get the advisory affected-selector
result. The selector's full or broad recommendations request an explicit bounded plan. They never schedule tests.
The protected judge replays the detached plan and checks the actual first attempt, job suite, uploaded artifact
identity and digest, exact case inventory, and source opportunities. The protected evidence reader accepts a single
authenticated GitHub API delegation to numeric production-results storage, without forwarding credentials or proxies.
The focused integration verifier takes the expected aggregate base from the queue and emits
`focused-main/v1` evidence for the exact current main run. The queue still checks that base and the tested PR source.

The existing release source policy reviews and pins the candidate workflow and recorder before candidate use.
The canonical path also needs hosted qualification of job identities, artifact reads, required checks and queue
freshness before activation.
