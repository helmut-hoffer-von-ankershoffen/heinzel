# AGENTS.md — CI/CD

| Workflow | Runs on | Does |
| --- | --- | --- |
| `ci.yml` | pull requests, `main`, daily, dispatch | lint, types, audit, tests (macOS/Linux × 3.12–3.15), e2e, template render, build; `CI OK` gates merging |
| `update-prs.yml` | push to `main`, dispatch | merges `main` into open auto-merging PRs that fell behind and approves the CI runs that triggers, and resolves their review threads of fixed or dismissed code-scanning alerts, so neither the up-to-date rule nor the resolved-threads rule stalls auto-merge |
| `release.yml` | dispatch | opens the release PR `chore(release): vX.Y.Z` and dispatches CI on it |
| `release-tag.yml` | push to `main` of a release commit | tags it and calls `publish.yml` |
| `publish.yml` | tags `v*`, called | builds, attests provenance, creates the GitHub release |
| `codeql.yml`, `scorecard.yml` | `main`, PRs, weekly | code scanning and supply-chain checks |
| `fuzz.yml` | PRs touching the contract parser, weekly, dispatch | fuzzes the workflow contract parser with atheris (`make fuzz`, core only) |
| `claude-review.yml` | PRs, opt-in (`vars.CLAUDE_REVIEW`) | reviewer agents comment GO / NO-GO |
| `demo.yml` | push to `main` touching the tape, README, `src/` or clock; dispatch | re-records `docs/assets/demo.gif` with VHS and opens the PR `docs: regenerate the demo` if it changed (core only: it runs `hz` commands) |

Pin actions to full SHAs with the tag as a comment (`gh api repos/<o>/<r>/commits/<tag> --jq .sha`), `permissions: {}`
at the top and least privilege per job, `persist-credentials: false`, untrusted `${{ }}` into `run:` only via `env:`.
Run scripts through an interpreter (`bash scripts/x.sh`, `uv run python scripts/x.py`), never by path: plugins get them
without the executable bit. `make lint` runs actionlint and zizmor. Workflows shared with plugins are symlinked into
`template/`; the template's `ci.yml` is generated from ours (`make fix`).
