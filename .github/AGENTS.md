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

Constraints per workflow:

- `ci.yml`: `CI OK` gates merging. PR portable tests run on Linux with Python oldest and newest plus real
  `macOS smoke`. Native/platform and unknown changes retain macOS setup, end-to-end and Electron checks, with failures
  blocking. The full matrix runs on main, daily and dispatch; every scheduled macOS job blocks `CI OK`, except the
  quarantine lane: `Quarantine (<os>)` runs only the tests in `tests/quarantine.toml` (at most 8, each at most 7 days,
  with a bead), retries and reports them, and is not in `CI OK`; the required `Quarantine registry` job fails on an
  entry past its `until`. Never add another job outside `CI OK`. A failing cheap check cancels the rest of
  a same-repository pull request's run as soon as it completes; fork PRs finish normally with read-only tokens. Every main
  commit keeps its own CI run, and a release PR's (head `release/*`) run is never cancelled. Explicit queue proofs may
  delegate intermediate commits to one uncancellable final full run with the aggregate base. Unknown proof runs full CI; admissions never claim CI OK or full-suite greens.
- `merge-gate.yml`: runs on `workflow_run` once a PR's CI finishes (no runner held while CI runs) from the default
  branch, never checks out PR code, and reports the required `merge-gate` and `coverage-floor` check runs on the PR
  head; a gate that did not pass or did not run fails closed.
- `update-prs.yml`: core keeps tested PR heads for the merge queue; plugin repositories update behind branches and
  approve the resulting CI runs. Closed code-scanning threads are resolved in both.
- `acceptance.yml`: `contents: read`, no secrets, publishes nothing; uploads `result.json`, which promotion reads. A
  candidate run (release wheel URL + sha256 + profile URL) is labelled `CANDIDATE_*`, never a public-path pass.
- `prepare-release.yml` (`make release`): opens the release PR `chore(release): vX.Y.Z` and dispatches CI on it (token-made
  PRs trigger none). A maintainer reviews all checks before merging.
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
- `fuzz.yml`: core only. `claude-review.yml`: opt-in (`vars.CLAUDE_REVIEW`).
- `docs.yml`: deploys to GitHub Pages when the repository is public or `vars.DOCS_PAGES` is `true`, else keeps an
  artifact (ADR 0082).
