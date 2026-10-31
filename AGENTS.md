# AGENTS.md

Heinzelmännchen (`hz`): a kernel plus modules, each operation exposed as a CLI command, MCP tool and REST endpoint.
[INTENT.md](INTENT.md) says what it is for, [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) how it is built, and
[docs/CODING_STANDARDS.md](docs/CODING_STANDARDS.md) is the source of truth for code, tests and commits.

## Rules

- A change serves INTENT.md and the accepted ADRs in [docs/adr/](docs/adr/). A new kind of let, a new head, a new
  dependency or a change that contradicts an ADR gets a new ADR first (skill `write-adr`). When a pull request builds an ADR, add its number to the ADR's `implemented-by` front matter
  and run `make docs`; the index table is generated, never hand-edited.
- Every test file sets exactly one level, `pytestmark = pytest.mark.unit` (or `integration`, `e2e`); the suite
  refuses anything else. Definitions: [CODING_STANDARDS.md#tests](docs/CODING_STANDARDS.md#tests).
- Pull requests only: branch from `origin/main`, never stack pull requests, open with `make pr`. Conventional commits
  everywhere, including the pull request title: it becomes the squash commit and drives the changelog and version.
- Git hooks, `make check` and CI enforce formatting, lint, strict types, import contracts and coverage. Fix the
  cause; never bypass a hook (`--no-verify`) or weaken a rule, type check or threshold to get green.
- The release process owns `CHANGELOG.md` and version numbers. A new dependency needs a reason in the commit body.
- The repository is public. No employer, customer, colleague or internal project names in files, commit messages,
  pull request titles or review comments; examples use made-up names. `scripts/check_confidentiality.py` checks a
  private term list; never quote a term it reports.
- Review a non-trivial change before finishing (skill `review`) and resolve every Critical and Required finding.
- When a maintainer sets a standard, corrects how something is done or repeats an instruction, codify it as the
  strongest check that can express it (test, lint, hook) and fix existing violations (skill `codify`).

## Commands

| Task | Command |
| --- | --- |
| Set up everything the build needs: deps, pinned tools in `.tools/bin` (`scripts/tools.toml`), git hooks | `make setup` |
| What is missing, and the command that fixes it | `make doctor` |
| Fix what tools can: lockfile, format, lint autofixes, generated docs, ATTRIBUTIONS.md and template files | `make fix` |
| Edit loop, seconds | `make check-fast` |
| Everything CI checks offline | `make check` |
| What CI checks, before pushing (`PREFLIGHT=full` adds e2e, audit) | `make preflight` |
| Tests by level | `make test-unit` / `make test-integration` / `make test-e2e` |
| Render, test and audit the plugin template (`AUDIT=0`: offline, no audit) | `make template-test` |
| Open the pull request: preflight, push, PR to `main`, auto-merge (`DRAFT=1`: a draft, no auto-merge; `BODY="..."` or `BODY_FILE=path`: a new one's description) | `make pr TITLE="feat(scope): ..."` |
| What failed in CI (waits for the running build) | `make ci-logs` |
| A small copy, docs or web UI fix, start to validated pull request, safe for a small model ([docs/SHIP_SMALL.md](docs/SHIP_SMALL.md), skill `ship-small`) | `uv run --script scripts/ship.py start SLUG` |
| Rebase onto the latest `main` | `make sync` |

Make targets print one line per passing step and the tail of a failing one; don't pipe them through `tail` or `grep`
(`VERBOSE=1` streams everything). Try the CLI with `uv run hz ...`; every operation takes `--json`, and all commands and
options are in [docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md).

## Picking up work

- For Sonar security remediation, read [docs/SONAR_SECURITY.md](docs/SONAR_SECURITY.md) first: it records the plugin
  investigations, verified installation boundaries, compatibility pitfalls and how to verify the live main badge.

- Work in a clone or worktree of your own, one writer per set of files; `make setup` first. Read the pull request
  (`gh pr view N`) and its CI (`gh pr checks N`) before touching a branch someone else started.
- Bring a pushed branch up to date with `git merge origin/main`, not a rebase: pull requests squash-merge, so merge
  commits on a branch are fine and nobody's history is rewritten. `make sync` rebases, so use it only before the
  first push.
- `main` requires branches to be up to date and `CI OK` green. Every merge sends the other open pull requests
  back through CI; `gh pr update-branch N` updates one without a local checkout.
- Big work merges dark: register a flag in `src/heinzel/kernel/_features.py`, check it with `feature_enabled`, and
  delete both when the work ships (ADR 0114). A flag hides behaviour, not state schema changes.
- ADR numbers collide when several pull requests are open. Before merging, check `docs/adr/` on `main` and take
  the next free number, fixing every reference (the README index, `amended-by` lines, docstrings).
- A failing check: `gh run view <run> --log-failed` works only after the whole run has finished. Rerun a job that
  failed for reasons outside the diff (a runner timeout) with `gh run rerun <run> --failed`; fix everything else.
- Core-root PR preflight, agent completion and pre-push hooks select owning checks for plugin-only changes.
  Mixed/core changes retain core checks. The pre-push hook also scopes by path (`scripts/scoped_checks.py push`):
  Markdown and site templates alone run the documentation checks and the tests that read documents, `web/` alone the
  web checks, everything else `make check-fast` in full; `make check-fast` itself stays the full run, and CI always
  runs the full gate. On a heavily loaded machine the unit step can hit the session
  timeout; wait for the load to drop and push again. `NO_PREFLIGHT=1 make pr` skips only the preflight, never a
  hook, and is for timeouts in tests you didn't touch, after review.
- UI changes must follow [Mobile UI](docs/CODING_STANDARDS.md#mobile-ui): use the shared viewport primitives and
  extend the automatic route/settings mobile sweep for new interactions; test Chromium and WebKit phone layouts.
- Web end-to-end tests run axe: fix contrast and target-size findings in tokens and components, never by
  relaxing a rule.

## Map

```
src/heinzel/kernel/   discovery, Module, lets, Kernel (registry + injector), settings; knows no head or module
src/heinzel/heads/    cli/ (Typer), mcp/, api/ (FastAPI), web/ (serves the app at / and api/ under /api): views
web/                  the web head's React app (Vite, TypeScript, pnpm from tools.toml); `make web-dev`, `make
                      web-build`; src/api/schema.d.ts is generated by `make fix`
src/heinzel/modules/  built-in modules: a package declaring `module = Module(name, summary)` is a module, nested
                      ones are submodules; nothing to register. Commands are `@module.operation` functions.
plugins/              optional local plugin source directory, discovered from project metadata only; public
                      plugins live in heinzel-plugins and build/test/release independently (ADR 0094).
                      Core never imports, installs or tests concrete plugin implementations.
starter/              minimal/contributions plugin templates; docs/PLUGIN_AUTHORING.md covers standalone public
                      and private repos and collection authors. Test against installed SDK/toolkit artifacts.
template/             retained governed copier template. Shared files are symlinks into this repo; its Makefile, CI,
                      pyproject, SECURITY.md and badges are generated (`make fix`), so edit the originals.
agent-skills/         the user-facing Claude Code plugin (skills hz-init, hz, hz-uninstall)
docs/                 MODULES.md, CLI_REFERENCE.md, CONFIGURATION.md and plugins/ (a page per plugin, ADR 0082) are
                      generated by `make fix`; don't hand-edit
```

[tests/](tests/AGENTS.md), [.github/](.github/AGENTS.md) and [src/heinzel/kernel/](src/heinzel/kernel/AGENTS.md)
have their own AGENTS.md.
