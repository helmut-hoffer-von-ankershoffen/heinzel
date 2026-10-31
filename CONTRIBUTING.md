# Contributing to Heinzelmännchen

<!-- badges:start -->
[![CI](https://img.shields.io/github/actions/workflow/status/helmut-hoffer-von-ankershoffen/heinzel/ci.yml?branch=main&label=CI)](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/helmut-hoffer-von-ankershoffen/heinzel/python-coverage-comment-action-data/endpoint.json)](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/tree/python-coverage-comment-action-data)
[![CodeQL](https://img.shields.io/github/actions/workflow/status/helmut-hoffer-von-ankershoffen/heinzel/codeql.yml?branch=main&label=CodeQL)](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://img.shields.io/ossf-scorecard/github.com/helmut-hoffer-von-ankershoffen/heinzel?label=OpenSSF%20Scorecard)](https://scorecard.dev/viewer/?uri=github.com/helmut-hoffer-von-ankershoffen/heinzel)
[![OpenSSF Best Practices](https://img.shields.io/cii/level/15863?label=OpenSSF%20Best%20Practices)](https://www.bestpractices.dev/en/projects/15863/passing)
[![PyPI](https://img.shields.io/pypi/v/heinzel)](https://pypi.org/project/heinzel/)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14%20%7C%203.15-blue)](pyproject.toml)
[![Quality Gate Status](https://img.shields.io/sonar/quality_gate/helmut-hoffer-von-ankershoffen_heinzel?server=https://sonarcloud.io&label=Quality%20Gate%20Status)](https://sonarcloud.io/summary/overall?id=helmut-hoffer-von-ankershoffen_heinzel)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=helmut-hoffer-von-ankershoffen_heinzel&metric=security_rating)](https://sonarcloud.io/summary/overall?id=helmut-hoffer-von-ankershoffen_heinzel)
[![License: MIT](https://img.shields.io/badge/license-MIT-brightgreen)](LICENSE)
<!-- badges:end -->

Thanks for improving Heinzelmännchen. People and coding agents take the same path, and the repository enforces it
for both: git hooks on your machine, CI on the pull request. This page gets you from a clone to a merged pull
request; [AGENTS.md](AGENTS.md) is the short version for agents.

## What you need

macOS (Apple silicon or Intel) or Linux (x86_64 or arm64) with:

- **git** and **make**. On macOS both come with the command line tools: `xcode-select --install`.
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)**: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  (or `brew install uv`). uv brings the right Python.

That is all. `make setup` installs everything else into the clone, without sudo: Python and every Python tool in
`.venv`, and the few programs that aren't Python packages (gitleaks, the GitHub CLI `gh`) pinned and checksum-verified
in `.tools/bin` ([ADR 0036](docs/adr/0036-the-build-installs-its-own-tools.md)). `make doctor` shows what is there and
prints the command that fixes anything missing. Windows: use WSL.

The web app uses TypeScript 7's native compiler through the pinned `@typescript/native` npm alias. Its schema
generator still requires TypeScript 5's JavaScript API, so `typescript` stays on the generator's supported major.
`make typecheck` and `pnpm run typecheck` select the native compiler explicitly; Node type definitions match the pinned
Node runtime's major. This follows [TypeScript's side-by-side migration guidance](https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/).

## Get going

1. **Fork and clone.** Fork the repository on GitHub, then:

   ```bash
   git clone https://github.com/<you>/heinzel && cd heinzel
   git remote add upstream https://github.com/helmut-hoffer-von-ankershoffen/heinzel
   ```

   With write access, clone the repository itself instead and skip `upstream`.

2. **Set up**, then check that everything works, in about a minute:

   ```bash
   make setup        # .venv, the pinned tools, git hooks; run it again any time, it only adds what's missing
   make check-fast   # format, lint, import contracts, generated files, unit tests
   ```

3. **Branch and change.** `git switch -c fix/short-description`, edit, and keep `make check-fast` green while you
   work. `make fix` repairs what tools can (formatting, lint autofixes, the lockfile, generated docs).

4. **Commit** with a [conventional commit](https://www.conventionalcommits.org) message: `fix(cli): exit 1 when the
   config is invalid`. The commit-msg hook checks it; the pre-commit hook formats, lints, type-checks and scans for
   credentials.

5. **Open the pull request**:

   ```bash
   gh auth login      # once; `gh` is in .tools/bin, and `make doctor` says whether you are logged in
   make pr TITLE="fix(cli): exit 1 when the config is invalid"
   ```

   `make pr` runs `make preflight` (what CI checks), pushes the branch to your fork and opens the pull request
   against `main`. The title must be a conventional commit too: it becomes the commit on `main`, the changelog entry
   and decides the next version.

`make help` lists every target. Targets are quiet, one line per passing step and the tail of a failing one;
`VERBOSE=1` streams everything. `make sync` rebases your branch onto the latest `main`, `make ci-logs` shows what
failed in CI.

## Run the CLI from your checkout

```bash
uv run hz --help
uv run hz system info --json
```

Every operation takes `--json`; all commands are in [docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md). To try it as a
user would, installed with uv: `uv tool install --editable .`.

## Tests

Every test is exactly one of `unit` (one module, mocked collaborators), `integration` (across layers, real files and
subprocesses, no network) or `e2e` (installs `hz` for real, needs network); the suite refuses anything else.
Definitions and conventions: [CODING_STANDARDS.md#tests](docs/CODING_STANDARDS.md#tests).

| Run | Command |
| --- | --- |
| The edit loop | `make check-fast` |
| Everything CI checks offline (lint, types, unit and integration) | `make check` |
| One level | `make test-unit`, `make test-integration`, `make test-e2e` |
| With coverage (at least 90 %, branch coverage) | `make test` |
| Every supported Python, 3.12 to 3.15 | `make test-all-pythons` |
| What CI runs, before you push | `make preflight` (`PREFLIGHT=full` adds e2e and the dependency audit) |

## What a pull request needs

A contribution is accepted when it meets all of these; reviewers and CI hold every pull request to them, the
maintainer's own included:

- **Tests.** New behaviour comes with tests at the right level, and a bug fix with a test that fails without it
  (a regression test). Coverage of unit and integration tests stays at least 90 % (branch coverage, `make test`
  fails below it), and new code should be fully covered; an uncovered line needs a reason.
- **The coding standards** in [docs/CODING_STANDARDS.md](docs/CODING_STANDARDS.md): formatted and linted with ruff,
  strict types with mypy and pyright, no warnings (the test suite turns every warning into an error), the
  architecture's import contracts. The hooks and `make check` enforce them; fix the cause, don't silence a check.
- **Docs in the same change.** Behaviour a user sees is documented where users look (README, `docs/`, the
  generated CLI reference); a structural change has an ADR.
- **A conventional commit title**, small enough to review in one sitting, and green CI (`CI OK`).
- **Your right to contribute it.** By opening a pull request you agree that your contribution is licensed under the
  project's [MIT license](LICENSE).

## Review and merging

- CI must pass: the `CI OK` check gates `main`. It runs lint and types, the tests on Linux for every
  Python, end-to-end tests, the plugin template, a secret scan, a dependency audit, CodeQL and `make setup` on every
  supported platform. Each macOS test cell is split into a unit and an integration shard under the same session cap.
  To spare macOS runners, a pull request runs macOS on the newest Python only, without Intel and end-to-end; `main` and
  dispatch (the release pull request) test macOS on the oldest and newest Python; the daily run tests all of them.
  Pull requests get a coverage comment.
- A maintainer reviews. Answer review comments in the thread and push fixes to the same branch; `main` requires
  resolved review threads and an up-to-date branch (a workflow merges `main` into auto-merging pull requests).
- Pull requests are squash-merged after review. `make pr` opens or updates the pull request without enabling
  auto-merge; a maintainer verifies the current head's checks and reviews before merging.
- Structural changes (a new kind of let, a new head, a new dependency, a changed public API, or a change that
  contradicts an [ADR](docs/adr/)) need an ADR first; open an issue to discuss before writing much code. With Claude
  Code, run the `review` skill before opening the pull request.

## Make a plugin

Company-, team- or site-specific features belong in a plugin, not in the core ([INTENT.md](INTENT.md), tenet 9).
`uv run hz system plugin new ../heinzel-<name>` scaffolds one with the same tooling, hooks, CI and agent setup
(every plugin is named `heinzel-<name>`, [ADR 0025](docs/adr/0025-plugin-distributions-are-named-heinzel-name.md));
inside it, `make setup` works as here and `make update-template` keeps it current. A small complete plugin to copy
from: [plugins/heinzel-clock](https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/tree/main/plugins/heinzel-clock). Operations, modules, settings and services: [README
Extend](README.md#extend) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); with Claude Code, the skills
`add-module` and `add-operation`.

### The README demo

The GIF at the top of the README is rendered by [VHS](https://github.com/charmbracelet/vhs) from
`docs/assets/demo.tape`. That tape is generated (`make fix`, and `make check` fails while it is stale) from
`docs/assets/demo-intro.tape` (settings, install, `hz --version`, `hz system info`) and the snippets modules contribute.
A built-in module or a first-party plugin adds its part as a `demo.tape` next to its `__init__.py`; abridged from
`plugins/heinzel-clock/src/heinzel_clock/demo.tape`:

```
# The clock plugin in the README demo: add it, show the clocks, then the same as JSON into jq.
Require jq

Type "hz system plugin add clock --confirm"
Enter
Wait@60s
Sleep 1.5s

Type "hz clock --json | jq"
Enter
Wait
Sleep 3s
```

A snippet may only use `Type`, `Enter`, `Wait`, `Sleep`, `Hide` and `Show`, and types only real `hz` commands (or
`clear`); `Require` names any other program it uses and moves to the header. Settings belong to the intro. Each
snippet starts on a cleared screen; the order is the intro, then first-party plugins, then built-in modules, each
sorted by path, so built-in commands such as `hz system doctor` close the demo. Keep a snippet to a few seconds.

`make demo` renders the GIF locally with the real `hz` from your checkout, installed into a throwaway uv tool
directory, so your own hz, plugins and settings stay out of it; it installs vhs, ffmpeg and jq (pinned, like the
build's tools) the first time, and on macOS needs `brew install ttyd`. Look at the last frames, then commit the GIF.
On `main`, the Demo workflow re-renders it on macOS whenever the tape changes, and opens a pull request.

## Rules worth knowing

- **Code:** [docs/CODING_STANDARDS.md](docs/CODING_STANDARDS.md) is the source of truth; hooks and CI enforce it.
  Fix the cause of a failing check; never bypass a hook (`--no-verify`) or loosen a rule to get green.
- **Commits:** `type(scope): summary`, the whole line at most 72 characters; types and scopes are in
  [CODING_STANDARDS.md](docs/CODING_STANDARDS.md#commits-branches-and-releases). A new dependency needs a reason in
  the commit body, and keeps [ATTRIBUTIONS.md](ATTRIBUTIONS.md) current: `make fix` regenerates it from uv.lock and
  web/pnpm-lock.yaml (`make attributions` alone), and `make check` fails while it lists other packages than they
  pin. The release process owns `CHANGELOG.md` and version numbers.
- **Scripts:** Python scripts in `scripts/` declare their dependencies inline
  ([PEP 723](https://peps.python.org/pep-0723/), pinned to uv.lock's versions) and run with
  `uv run --script scripts/x.py`, outside the project environment; one that imports heinzel runs with
  `uv run python` and says why in a `# Project-bound:` comment. Shell scripts run with `bash scripts/x.sh`, never by
  path: plugins get them without the executable bit. `tests/unit/scripts_test.py` checks both.
- **Confidentiality:** the repository is public and generic; examples use made-up names, and nothing may name a
  company, customer, colleague or internal project. Maintainers check against a private list of terms
  (`~/.config/heinzel-dev/confidential-terms`, or `$CONFIDENTIAL_TERMS_FILE`; without one the check is skipped
  locally). Credentials are a separate check: gitleaks runs on every commit and over the whole history in CI.
- **Coding agents:** Claude Code, Cursor, Codex, GitHub Copilot, Windsurf and Zed read [AGENTS.md](AGENTS.md) by
  themselves (Claude Code through CLAUDE.md); Gemini CLI needs `"context": {"fileName": ["AGENTS.md"]}` in its
  settings, Aider `read: AGENTS.md` in `.aider.conf.yml`.

## Questions

- Bugs and feature ideas: [open an issue](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/issues/new/choose).
  The bug form asks for what happened, the steps to reproduce it and the output of `hz --version` and
  `hz system doctor --json`; search [the open and closed issues](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/issues?q=is%3Aissue)
  first. A maintainer answers within 14 days.
- A question about how to do something: an issue is fine too; or open a draft pull request (`make pr DRAFT=1
  TITLE="..."`) and ask in its description.
- Security vulnerabilities: privately, as [SECURITY.md](SECURITY.md) describes, never in a public issue.
- Something in the setup doesn't work: run `make doctor` and paste its output into the issue.

Be kind, assume good intent, critique code and ideas, not people ([CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)).

## For maintainers

**Releases** are pull requests ([ADR 0013](docs/adr/0013-pull-request-based-development-and-releases.md)): `make release`
(`BUMP=minor`; or Actions → **Prepare release** → Run workflow) dispatches a GitHub runner that derives the bump from
the commits (`auto`, or force `patch`, `minor`, `major`), prepares the branch and dispatches CI on it, so nothing runs
on your machine (`make release-local` is the same script locally). It opens `chore(release): vX.Y.Z` with the version
and `CHANGELOG.md` updated. The required **intro** (`make release INTRO="..."`) becomes the release's introduction
under its heading in `CHANGELOG.md` and in the release notes; edit the pull request to change it. Introductions of
earlier releases survive regeneration; hand edits to their entries don't, because entries come from the commits.
A maintainer reviews the release and verifies all checks on its current head before merging. The merge tags the
commit, and **Publish** builds wheel and sdist, attests their provenance and creates the GitHub
release with the changelog section as notes.

**Source review** of a pull request by an independent, tool-free model: `make review PR=<n>` (needs `REVIEW_TRUSTED`,
the commit of the protected ref `trusted/source-review` that you have read). It chooses a tier from the changed paths
([scripts/review_tier.py](scripts/review_tier.py): full for installer, authentication, approvals, release and workflow
code; light for other code; none for documentation, tests and Markdown, which costs nothing), dispatches
[Source review](.github/workflows/source-review.yml) on that ref and prints the run URL. The run fails closed unless
the live pull request still has the dispatched head and base, before and after the model; it never runs the target's code.

**Pinned tools** live in [scripts/tools.toml](scripts/tools.toml); to bump one, change its version and run
`uv run --script scripts/tools.py pin <name>`.

**Repository settings** the flow depends on:

- A ruleset on `main`: pull requests only (squash), required status check `CI OK` on an up-to-date branch, linear
  history, resolved review threads, no force pushes or deletions. Version tags are created by the Release tag
  workflow; Publish refuses a tag that doesn't match the project version.
- Merges: squash only, commit title = pull request title, delete branches after merge; review and current-head checks precede merging.
- Actions: workflow permissions read-only by default, "Allow GitHub Actions to create and approve pull requests" on.
- Security: private vulnerability reporting, secret scanning with push protection, Dependabot alerts and updates.
- Confidentiality: secret `CONFIDENTIAL_TERMS` for Actions and for Dependabot (its pull requests only see
  Dependabot secrets): `gh secret set CONFIDENTIAL_TERMS < ~/.config/heinzel-dev/confidential-terms`, again with
  `--app dependabot`. CI on this repository requires it.
- Reviews on GitHub: secret `CLAUDE_CODE_OAUTH_TOKEN` (run `claude setup-token`; usage counts against that Claude
  subscription, not API credits) and variable `CLAUDE_REVIEW=true`.
