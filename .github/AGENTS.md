# AGENTS.md — CI/CD

| Workflow | Runs on | Does |
| --- | --- | --- |
| `ci.yml` | pull requests, `main`, daily, dispatch | lint, types, audit, secret scan, tests (Linux × 3.12–3.15; macOS × 3.12–3.15 on `main`, daily and dispatch, 3.15 only on pull requests), e2e (Linux; macOS too off pull requests), template render, build, `make setup` + `make doctor` on Linux and macOS × x86_64/arm64 and `make tools-verify` (macOS Intel off pull requests), the notifier app (on pull requests only when its sources change, decided by the `changes` job); `CI OK` gates merging |
| `update-prs.yml` | push to `main`, dispatch | merges `main` into open auto-merging PRs that fell behind and approves the CI runs that triggers, and resolves their review threads of fixed or dismissed code-scanning alerts, so neither the up-to-date rule nor the resolved-threads rule stalls auto-merge |
| `prepare-release.yml` | dispatch (`make release`) | opens the release PR `chore(release): vX.Y.Z`, dispatches CI on it (token-made PRs trigger none; the dispatched run reports `CI OK`) and enables squash auto-merge |
| `release-tag.yml` | push to `main` of a release commit | tags it and calls `publish.yml` |
| `publish.yml` | tags `v*`, called | builds, attests provenance, creates the GitHub release |
| `codeql.yml`, `scorecard.yml` | `main`, PRs, weekly | code scanning and supply-chain checks |
| `fuzz.yml` | PRs touching the contract parser, weekly, dispatch | fuzzes the workflow contract parser with atheris (`make fuzz`, core only) |
| `claude-review.yml` | PRs, opt-in (`vars.CLAUDE_REVIEW`) | reviewer agents comment GO / NO-GO |
| `docs.yml` | push to `main` changing `docs/scenes.toml` or `docs/assets/demo.tape`, dispatch (re-record); called by `release-tag.yml` on a release (site only) | re-records every plugin page's scenes and the README's intro (`make docs-record`) and opens the PR `docs: re-record the plugin pages`; builds the MkDocs site and deploys it to GitHub Pages when the repository is public or `vars.DOCS_PAGES` is `true`, else keeps it as an artifact (ADR 0082; shared with plugins) |

Pin actions to full SHAs with the tag as a comment (`gh api repos/<o>/<r>/commits/<tag> --jq .sha`), `permissions: {}`
at the top and least privilege per job, `persist-credentials: false`, untrusted `${{ }}` into `run:` only via `env:`.
Run scripts through an interpreter (`bash scripts/x.sh`, `uv run --script scripts/x.py`), never by path: plugins get them
without the executable bit. `make lint` runs actionlint and zizmor. Workflows shared with plugins are symlinked into
`template/`; the template's `ci.yml` is generated from ours (`make fix`).
