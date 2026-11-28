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

## Release and CI policy (owner decisions)

Dated owner decisions: the policy the gates must allow. Implemented by #907 (release chain, `AUTO_CUT`) and #933 (PR
upgrade lane and macOS selection); until they merge, the current workflows apply.

- 2026-11-22, owner: after a PR merges, CI does the release. A release chain cuts, merges the release PR, tags,
  publishes to PyPI, composes, runs acceptance and marks Latest without a person, behind the same gates: required
  `CI OK`, SonarCloud and Gate review on every PR, the candidate verdict, first-attempt acceptance receipts, Latest
  only after acceptance, and the release freeze. Manual dispatch (`sm release VERSION`) is the default; scheduled cuts
  are enabled by the owner's word only. The maintainer reviews the change PRs and through Gate review, not the release
  PR.
- 2026-11-22, owner/SDLC (packets 055-ACTIONS item 9): a PR runs macOS unit and integration only when an upgrade
  selector picks update, lifecycle, kernel/spec or upgrade paths. Every PR still runs the full Linux suite and an
  in-place upgrade job; main's coalesced macOS lane runs on every main commit; release candidates run everything on
  both OSes.
- 2026-11-23, owner: the in-place upgrade job, like the macOS unit and integration cells, runs on a PR only when the
  upgrade selector picks it; it always runs on main and on release commits. This supersedes the "in-place upgrade job
  on every PR" half of the 2026-11-22 entry above.

- 2026-11-23, owner: the in-place upgrade job, like the macOS unit and integration cells, runs on a PR only when the
  upgrade selector picks it; it always runs on main and on release commits. This supersedes the "in-place upgrade job
  on every PR" half of the 2026-11-22 entry above.

- Repeat a hosted-only failure in one focused job on the failing runner. This Linux example uses stress to reproduce
  a timing failure. `--throwaway` dispatches on a new validation commit with the same tree as `BRANCH`.

  ```sh
  sm ci one dispatch BRANCH --test 'tests/e2e/docs_test.py::test_readme_instructions' \
    --kind e2e --os ubuntu-24.04 --repeat 10 --stress --throwaway --wait --cleanup
  ```

  Use `sm ci one repeats --fail-rate P` to choose the repeat count from the observed failure rate. If the focused
  repeat passes, read the original failed job's log and record the cause before rerunning it with
  `sm ci one rerun RUN --job NAME`, using its exact job name. If the repeat fails, fix the cause. If the same check
  fails twice, read its log and record the cause before pushing again. The focused result is diagnostic evidence
  only. Merge still requires the normal CI checks.

## Picking up work

- Sonar security remediation: read [docs/SONAR_SECURITY.md](docs/SONAR_SECURITY.md) first.
- Work in your own clone or worktree, one writer per set of files. Read `gh pr view N` and `gh pr checks N` before
  touching a branch someone else started.
- When a pushed branch needs an update, use `git merge origin/main`, not a rebase (PRs squash-merge).
  Core's merge queue uses each PR's tested head without an automatic update to `main`; green `CI OK` remains required.
- Big work merges dark: register a flag in `src/heinzel/kernel/ai/_features.py`, check it with `feature_enabled`, delete both
  when it ships (ADR 0115). A flag hides behaviour, not state schema changes.
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
