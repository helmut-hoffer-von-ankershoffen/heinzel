# AGENTS.md — CI/CD

- Pin actions to full SHAs with the tag as a comment (`gh api repos/<o>/<r>/commits/<tag> --jq .sha`).
- Set `permissions: {}` at the top and least privilege per job; set `persist-credentials: false`.
- Pass untrusted `${{ }}` into `run:` only via `env:`.
- Run scripts through an interpreter (`bash scripts/x.sh`, `uv run --script scripts/x.py`), never by path: plugins get
  them without the executable bit.
- `make lint` runs actionlint and zizmor.
- Workflows shared with plugins are symlinked into `template/`; the template's `ci.yml` is generated from ours
  (`make fix`).

Constraints per workflow:

- `ci.yml`: `CI OK` gates merging. The full matrix runs on `main`, daily and dispatch; pull requests run a subset.
- `acceptance.yml`: `contents: read`, no secrets, publishes nothing; uploads `result.json`, which promotion reads. A
  candidate run (release wheel URL + sha256 + profile URL) is labelled `CANDIDATE_*`, never a public-path pass.
- `prepare-release.yml` (`make release`): opens the release PR `chore(release): vX.Y.Z` and dispatches CI on it (token-made
  PRs trigger none). A maintainer reviews all checks before merging.
- `edge.yml`: dark until `vars.RELEASE_CHANNELS` is set (ADR 0114); then publishes pre-releases `edge-v<version>`,
  never latest, never on PyPI.
- `pypi.yml`: manual dispatch on a final version tag; builds without OIDC, uploads only core with trusted publishing
  in environment `pypi`.
- `release.yml`: manual dispatch for one optional plugin; never builds or tests core.
- `source-review.yml`: dispatch on the protected ref `trusted/source-review` only (`make review PR=<n>`); paid, tool-free
  review of one PR's source; fails closed unless live head, base and protected ref equal the dispatch; core only.
- `fuzz.yml`: core only. `claude-review.yml`: opt-in (`vars.CLAUDE_REVIEW`).
- `docs.yml`: deploys to GitHub Pages when the repository is public or `vars.DOCS_PAGES` is `true`, else keeps an
  artifact (ADR 0082).
