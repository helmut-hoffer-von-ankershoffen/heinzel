# AGENTS.md

Heinzelmännchen (`hz`): a kernel plus modules; each operation is a CLI command, MCP tool and REST endpoint.
Read [INTENT.md](INTENT.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and
[docs/CODING_STANDARDS.md](docs/CODING_STANDARDS.md) (code, tests, commits).

## Commands

Run `make setup` first. `make doctor` names what is missing and the fix.

| Task | Command |
| --- | --- |
| Fix lockfile, format, lint, generated docs, ATTRIBUTIONS.md, template files | `make fix` |
| Edit loop, seconds | `make check-fast` |
| Everything CI checks offline | `make check` |
| CI checks before pushing (`PREFLIGHT=full` adds e2e, audit) | `make preflight` |
| Tests by level | `make test-unit` / `make test-integration` / `make test-e2e` |
| Render, test, audit the plugin template (`AUDIT=0`: offline) | `make template-test` |
| Open the PR (`DRAFT=1`; `BODY="..."` or `BODY_FILE=path` for a new description) | `make pr TITLE="feat(scope): ..."` |
| Why CI failed (waits for the running build) | `make ci-logs` |
| Small copy, docs or web UI fix to validated PR (skill `ship-small`, [docs/SHIP_SMALL.md](docs/SHIP_SMALL.md)) | `uv run --script scripts/ship.py start SLUG` |
| Rebase onto `main` (before the first push only) | `make sync` |

Never pipe make targets through `tail` or `grep` (`VERBOSE=1` streams everything). Try the CLI with `uv run hz ...`;
every operation takes `--json`; options in [docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md).

## Rules

- A new kind of let, a new head, a new dependency or a change that contradicts an ADR in [docs/adr/](docs/adr/) needs a
  new ADR first (skill `write-adr`).
- A PR that builds an ADR adds its number to the ADR's `implemented-by`, sets `implementation` (`built`, `partial`,
  `planned`) to what the code now does, and runs `make docs`. Never hand-edit the generated index table.
- Pull requests only: branch from `origin/main`, never stack, open with `make pr`. Conventional commits and PR titles:
  the title becomes the squash commit and drives changelog and version.
- Fix the cause of a failing hook or check. Never use `--no-verify`; never weaken a rule, type check or threshold.
- Leave `CHANGELOG.md` and version numbers to the release process. Give a new dependency a reason in the commit body.
- The repository is public. No employer, customer, colleague or internal project names in files, commits, PR titles
  or review comments; use made-up names. Never quote a term `scripts/check_confidentiality.py` reports.
- Review a non-trivial change before finishing (skill `review`); resolve every Critical and Required finding.
- When a maintainer sets a standard, corrects you or repeats an instruction, codify it as the strongest check that can
  express it (test, lint, hook) and fix existing violations (skill `codify`).
- UI: follow [Mobile UI](docs/CODING_STANDARDS.md#mobile-ui). Use the shared viewport primitives, extend the
  route/settings mobile sweep for new interactions, test Chromium and WebKit phone layouts. Fix axe contrast and
  target-size findings in tokens and components, never by relaxing a rule.

## Picking up work

- Sonar security remediation: read [docs/SONAR_SECURITY.md](docs/SONAR_SECURITY.md) first.
- Work in your own clone or worktree, one writer per set of files. Read `gh pr view N` and `gh pr checks N` before
  touching a branch someone else started.
- Update a pushed branch with `git merge origin/main`, not a rebase (PRs squash-merge). `gh pr update-branch N` does it
  without a checkout; `main` requires an up-to-date branch and green `CI OK`.
- ADR numbers collide across open PRs. Before merging, take the next free number on `main` and fix every reference
  (README index, `amended-by` lines, docstrings).
- `gh run view <run> --log-failed` works only after the whole run finishes. Rerun a job that failed outside the diff
  with `gh run rerun <run> --failed`; fix everything else.
- The pre-push hook and `make pr` scope checks to the changed paths (`scripts/scoped_checks.py push`); CI and a manual
  `make check-fast` run everything. If the unit step times out on a loaded machine, wait and push again.
  `NO_PREFLIGHT=1 make pr` skips only the preflight, for timeouts in tests you didn't touch, after review.

## Map

```
src/heinzel/kernel/   discovery, Module, lets, Kernel, settings; knows no head or module
src/heinzel/heads/    cli/ (Typer), mcp/, api/ (FastAPI), web/ (serves the app at / and api/ under /api)
src/heinzel/modules/  built-in modules: a package declaring `module = Module(name, summary)`; nested ones are
                      submodules; nothing to register; commands are `@module.operation` functions
web/                  the web head's React app; `make web-dev`, `make web-build`
plugins/              optional local plugin sources. Public plugins live in heinzel-plugins (ADR 0094); core never
                      imports, installs or tests concrete plugins
starter/              plugin templates; docs/PLUGIN_AUTHORING.md. Test against installed SDK/toolkit artifacts
template/             copier template. Shared files are symlinks into this repo; Makefile, CI, pyproject,
                      SECURITY.md and badges are generated: edit the originals
agent-skills/         the user-facing Claude Code plugin (skills hz-init, hz, hz-uninstall)
```

Never hand-edit generated files: `docs/MODULES.md`, `CLI_REFERENCE.md`, `CONFIGURATION.md`, `docs/plugins/`,
`web/src/api/schema.d.ts` (`make fix` regenerates them).

Scoped instructions: [tests/](tests/AGENTS.md), [.github/](.github/AGENTS.md),
[src/heinzel/kernel/](src/heinzel/kernel/AGENTS.md).
