# Contributing to Heinzelmännchen

[![CodeQL](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/workflows/codeql.yml/badge.svg)](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/helmut-hoffer-von-ankershoffen/heinzel/badge)](https://scorecard.dev/viewer/?uri=github.com/helmut-hoffer-von-ankershoffen/heinzel)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14%20%7C%203.15-blue)](pyproject.toml)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Types: mypy + pyright strict](https://img.shields.io/badge/types-mypy%20%2B%20pyright%20strict-blue)](docs/CODING_STANDARDS.md#python)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-fe5196)](https://www.conventionalcommits.org)

Thanks for improving Heinzelmännchen. Contributions from people and from coding agents follow the same path; the repository
enforces it for both. If you are an agent, also read [AGENTS.md](AGENTS.md).

Coding agents: Claude Code, Cursor, Codex, GitHub Copilot, Windsurf and Zed read [AGENTS.md](AGENTS.md) by
themselves (Claude Code through CLAUDE.md). Gemini CLI needs `"context": {"fileName": ["AGENTS.md"]}` in its
settings, Aider `read: AGENTS.md` in `.aider.conf.yml`. Git hooks and CI apply to all of them.

## Before you start

- Read [INTENT.md](INTENT.md): what Heinzelmännchen is for and the tenets that decide trade-offs.
- Skim [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and the [ADRs](docs/adr/).
- Company-, site- or team-specific features belong in a plugin, not in the core (tenet 9). Scaffold one with
  `hz system plugin new ../heinzel-<name>`; every plugin is named `heinzel-<name>`
  ([ADR 0025](docs/adr/0025-plugin-distributions-are-named-heinzel-name.md)).
- For structural changes (new kind of let, new head, new dependency, changed public API), open an issue or a pull
  request with an ADR first.

## Set up

Requirements: [uv](https://docs.astral.sh/uv/) and `make`. uv installs the right Python by itself.

```bash
git clone https://github.com/helmut-hoffer-von-ankershoffen/heinzel && cd heinzel
make setup              # uv sync + git hooks (prek: format/lint/types on commit, conventional commit messages)
uv run hz --help       # the CLI from your checkout
```

## The loop

| Step | Command |
| --- | --- |
| Fix what tools can fix (lockfile, format, lint autofixes, generated files) | `make fix` |
| Everything CI checks offline (lint, import contracts, template sync, types, tests + coverage) | `make check` |
| What CI checks, before pushing (adds template, build, commit messages; `PREFLIGHT=full` adds e2e, audit) | `make preflight` |
| Open the pull request (preflight, push, PR to `main`, auto-merge) | `make pr TITLE="feat: ..."` |
| What failed in CI, waiting for a running build | `make ci-logs` |
| Unit / integration / end-to-end tests | `make test-unit` / `make test-integration` / `make test-e2e` |
| All supported Pythons (3.12–3.15), each in an isolated environment | `make test-all-pythons` |
| Render the plugin template and check the result | `make template-test` |
| Regenerate the template's derived files after changing Makefile, CI or pyproject | `make template-sync` |
| Dependency audit | `make audit` |
| Preview the changelog | `make changelog` |

`make help` lists everything. Targets are quiet: one line per passing step, the tail of a failing one; `VERBOSE=1`
streams all output.

We don't use nox or tox: uv creates an isolated environment per Python version in seconds, so `make test-all-pythons`
is a loop over `uv run --isolated --python 3.X`, and CI runs the same tests in a matrix on macOS and Linux.

## Tests

Every test is exactly one of `unit`, `integration` or `e2e` — definitions in
[CODING_STANDARDS.md](docs/CODING_STANDARDS.md#tests), adopted from the Aignostics Python SDK. The suite refuses tests
without exactly one marker. Coverage of unit + integration must stay ≥ 90 % (branch coverage); pull requests get a
coverage comment.

End-to-end tests install Heinzelmännchen for real with `uv tool install` into a sandbox and run the full lifecycle: plugins,
upgrade, update, uninstall — both directly and through the scripts of the `/hz-init` and `/hz-uninstall` skills.

## Confidentiality

Heinzelmännchen is public and generic: examples use made-up names, and nothing in files or commit messages may
reveal a company, customer, colleague or internal project. Maintainers keep a private list of such terms, outside the
repository: one term per line in `~/.config/heinzel-dev/confidential-terms` (or the file in
`$CONFIDENTIAL_TERMS_FILE`), and the same list in the repository secret `CONFIDENTIAL_TERMS`. The pre-commit and
commit-msg hooks, `make lint`, CI (files, commit messages, pull request title) and Publish check against it and report
locations and term numbers only. Without a list, the check is skipped locally; CI on this repository requires it.

Credentials (API keys, tokens, private keys) are a separate check: [gitleaks](https://github.com/gitleaks/gitleaks)
runs on every commit and scans the whole history in CI, with GitHub secret scanning and push protection behind it.

## Pull requests

`main` only changes through pull requests.

1. Branch from `main`: `git switch -c feat/short-description`.
2. Commit with [Conventional Commits](https://www.conventionalcommits.org): `feat(kernel): ...`, `fix(plugin): ...`,
   `docs: ...`. The commit-msg hook checks it.
3. Run `make check`; with Claude Code, run the `review` skill (critic, test-engineer, security-reviewer, architect).
4. Open the pull request; its **title** must be a conventional commit — it becomes the squash commit on `main`, feeds
   the changelog and decides the next version.
5. CI must pass (`CI OK`). CodeQL and, if enabled, the Claude review run too. Pull requests are squash-merged and the
   branch is deleted.

## Releases

Fully automated and pull-request based ([ADR 0013](docs/adr/0013-pull-request-based-development-and-releases.md)):

1. Actions → **Release** → Run workflow (`auto` derives the bump from commits; or force `patch`/`minor`/`major`).
   It opens a pull request `chore(release): vX.Y.Z` that updates the version everywhere and `CHANGELOG.md`, and
   runs CI on it. The optional **intro** becomes the release's introduction under its heading in `CHANGELOG.md` and
   in the release notes; edit it in the pull request like the rest. Introductions of earlier releases survive the
   regeneration; hand edits to their entries don't, because entries come from the commits.
2. Review and merge it. **Release tag** then tags the merge commit and **Publish** builds wheel and sdist, attests
   their provenance and creates the GitHub release with the changelog section as notes.

`make release` (`make release INTRO="..."`) opens the same pull request from a workstation.

## Repository settings (maintainers)

The release and pull-request flow depends on these settings of the GitHub repository:

- A ruleset on `main`: pull requests only (squash), required status check `CI OK` on an up-to-date branch, linear
  history, resolved review threads, no force pushes or deletions. Version tags are created by the Release tag
  workflow; Publish refuses a tag that doesn't match the project version. (A tag ruleset that only GitHub Actions may
  bypass needs an organization-owned repository.)
- Merges: squash only, commit title = pull request title, delete branches after merge, auto-merge allowed.
- Actions: workflow permissions read-only by default, "Allow GitHub Actions to create and approve pull requests" on.
- Security: private vulnerability reporting, secret scanning with push protection, Dependabot alerts and updates.
- Confidentiality: secret `CONFIDENTIAL_TERMS` for Actions and for Dependabot (its pull requests only see
  Dependabot secrets): `gh secret set CONFIDENTIAL_TERMS < ~/.config/heinzel-dev/confidential-terms`, again with
  `--app dependabot`.
- Reviews on GitHub: secret `CLAUDE_CODE_OAUTH_TOKEN` (run `claude setup-token`; usage counts against that Claude
  subscription, not API credits) and variable `CLAUDE_REVIEW=true`.

## Extending Heinzelmännchen

- **Operation** (command): `@module.operation` on a typed function — skill `add-operation`.
- **Module / submodule:** a package with `module = Module(...)` — skill `add-module`.
- **Settings:** `@module.settings` on a `ModuleSettings` subclass; read from `[<module>]` in config files and
  `HZ_<MODULE>__*`.
- **Service:** `module.service(MyService)`; injected wherever `MyService` is asked for.
- **Info section:** `@module.info` contributes to `hz system info`.
- **Plugin:** `hz system plugin new <dir>`; keep it current with `make update-template` inside the plugin.
- **New kind of let or head:** write an ADR (skill `write-adr`) first.

## Code of conduct

Be kind, assume good intent, critique code and ideas, not people.
