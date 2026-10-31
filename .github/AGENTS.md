# AGENTS.md — CI/CD

| Workflow | Runs on | Does |
| --- | --- | --- |
| `ci.yml` | pull requests, `main`, daily, dispatch | lint, types, audit, secret scan, tests (Linux × 3.12–3.15; macOS × 3.12–3.15 on `main`, daily and dispatch, 3.15 only on pull requests), e2e (Linux; macOS too off pull requests), template render, build, `make setup` + `make doctor` on Linux and macOS × x86_64/arm64 and `make tools-verify` (macOS Intel off pull requests), the notifier app (on pull requests only when its sources change, decided by the `changes` job); `CI OK` gates merging |
| `acceptance.yml` | PRs touching the harness or workflow (a dry run against what is published), dispatch, called | public installer acceptance (`scripts/acceptance/`, core only) on hosted macOS and Linux: literal `uvx`/`curl` installs in private homes, lifecycle cases, default-is-stable; `contents: read`, no secrets, publishes nothing; uploads `result.json` as the receipt promotion reads. A candidate run (release wheel URL + sha256 + profile URL) is labelled `CANDIDATE_*`, never a public-path pass |
| `update-prs.yml` | push to `main`, dispatch | merges `main` into open auto-merging PRs that fell behind and approves the CI runs that triggers, and resolves their review threads of fixed or dismissed code-scanning alerts, so neither the up-to-date rule nor the resolved-threads rule stalls auto-merge |
| `prepare-release.yml` | dispatch (`make release`) | opens the release PR `chore(release): vX.Y.Z` and dispatches CI on it (token-made PRs trigger none; the dispatched run reports `CI OK`); a maintainer reviews all checks before merging |
| `release-tag.yml` | push to `main` of a release commit | tags it and calls `publish.yml` |
| `publish.yml` | tags `v*`, called | builds, attests provenance, creates the GitHub release |
| `edge.yml` | green CI on `main`; pull requests changing it | dark until `vars.RELEASE_CHANNELS` is set (ADR 0114); then publishes each releasable commit as a GitHub pre-release `edge-v<version>`, never latest and never on PyPI; otherwise only builds and verifies |
| `pypi.yml` | manual dispatch on a final version tag | validates a published core GitHub release, builds without OIDC, verifies artifacts, then uploads only core with trusted publishing in environment `pypi` |
| `release.yml` | manual dispatch for one optional source plugin | owning checks, wheel acceptance and protected publication through the versioned toolkit; never builds or tests core |
| `codeql.yml`, `scorecard.yml` | `main`, PRs, weekly | code scanning and supply-chain checks |
| `fuzz.yml` | PRs touching the contract parser, weekly, dispatch | fuzzes the workflow contract parser with atheris (`make fuzz`, core only) |
| `claude-review.yml` | PRs, opt-in (`vars.CLAUDE_REVIEW`) | reviewer agents comment GO / NO-GO |
| `docs.yml` | documentation changes on PRs and `main`, hourly public catalog refresh, dispatch (optional re-record), release (site only) | re-records every plugin page's scenes and the README's intro (`make docs-record`) and opens the PR `docs: re-record the plugin pages`; builds the MkDocs site and deploys it to GitHub Pages when the repository is public or `vars.DOCS_PAGES` is `true`, else keeps it as an artifact (ADR 0082; shared with plugins) |

Pin actions to full SHAs with the tag as a comment (`gh api repos/<o>/<r>/commits/<tag> --jq .sha`), `permissions: {}`
at the top and least privilege per job, `persist-credentials: false`, untrusted `${{ }}` into `run:` only via `env:`.
Run scripts through an interpreter (`bash scripts/x.sh`, `uv run --script scripts/x.py`), never by path: plugins get them
without the executable bit. `make lint` runs actionlint and zizmor. Workflows shared with plugins are symlinked into
`template/`; the template's `ci.yml` is generated from ours (`make fix`).
