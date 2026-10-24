# Coding standards

These standards apply to Heinzelmännchen and to every plugin scaffolded from its template. Tools enforce most of them; where a
tool and this document disagree, this document wins and the tool configuration is the bug.

Each rule has a reason. If a rule gets in your way, change the rule in a pull request — don't work around it.

## Principles

1. **Explicit beats magic, declared beats discovered-by-convention.** Modules declare `module = Module(...)`;
   operations are decorated; services are registered by type. The kernel finds declarations; it never guesses.
2. **One way to do a thing.** One way to add a command (an operation), one way to inject (by type), one way to fail
   (`HeinzelError`), one way to configure (`HZ_*` settings).
3. **Small, composable pieces.** A module does one thing. A function does one thing. Prefer adding a module over
   growing one.
4. **Heads are views.** Business logic never knows whether it is called from a terminal, an MCP client or HTTP.
5. **Agents are first-class users.** Every operation has a docstring, typed parameters with descriptions, and a
   JSON-serializable result, so an agent can call it as reliably as a person.

## Python

- **Version:** Python 3.12+. Use modern syntax: PEP 695 generics (`def f[T](x: T) -> T`), `X | None`,
  `match` where it reads better than `if` chains.
- **Typing:** everything in `src/` is fully typed and passes `mypy --strict` and `pyright` strict. `Any` is allowed
  only for free-form JSON and forwarded `**kwargs`, with a `# noqa: ANN401` stating which. `cast` needs a reason
  if it isn't obvious. No `# type: ignore` without an error code.
- **Configuration:** `ModuleSettings` subclasses declared with `@module.settings`, every field with a
  `description`; never read `os.environ` directly in modules.
- **Data:** pydantic models for anything that crosses a boundary (operation results, settings, files).
  Frozen dataclasses with `slots=True` for internal value objects.
- **Errors:** raise `HeinzelError` with a message written for the person or agent who will read it — what went wrong
  and what to do. When the input was wrong, "what to do" is a command line that would work, for the same key or
  argument the user typed (e.g. `hz system config set clock.zones '["Europe/Berlin", "Asia/Tokyo"]'`). Other
  exceptions are bugs. Catch broad exceptions only at isolation boundaries and comment why.
- **Side effects:** no work at import time beyond declarations. No global mutable state except declared module
  objects. Subprocesses take argument lists, never `shell=True`.
- **Names:** say what a thing is (`PluginService`, `installed()`), not how it is built. Private helpers start with
  `_`; modules expose their API through `__init__.py` and `__all__`.
- **Docstrings and comments:** see [Comments and docstrings](#comments-and-docstrings).
- **Formatting and lint:** `ruff format` and `ruff check` with `select = ["ALL"]`. Ignored rules are listed with a
  reason in `pyproject.toml`. Line length 120.

## Comments and docstrings

Code says *what* and *how*; docstrings say what something is for; comments say *why*. Each rule below is checked by a
tool where one exists, and by the `critic` agent in review otherwise.

| Rule | Checked by |
| --- | --- |
| Every public module, class, function and method has a Google-style docstring | ruff `D` |
| The first line is one sentence that works as help text: operations' first lines *are* the CLI help, the MCP tool description and the OpenAPI summary | ruff `D`, `critic` |
| `Raises:` lists what a caller can catch, including exceptions raised by callees; `Yields:` says what a generator yields | ruff `DOC501`, `DOC402` (pydoclint) |
| `Args:` only where names and types don't say it all; operation parameters are described with `Field(description=...)` instead, because every head shows it | `critic` |
| `Returns:` only when the type and summary line don't make it obvious | `critic` |
| Comments explain *why* — a constraint, a trade-off, a link to an ADR or an upstream issue — never *what* the next line does. If a *what* comment seems needed, rename or extract instead | `critic` |
| No commented-out code; delete it, git remembers | ruff `ERA001` |
| TODOs are `# TODO(owner): what and why`, ideally with an issue link; they are debts, not decoration | ruff `TD` |
| Every `noqa` and `type: ignore` names its rule and says why: `# noqa: BLE001 - isolate broken plugins` | ruff `PGH`, mypy `ignore-without-code`, `critic` |
| No change-log comments ("added X", "fixed Y"); that's what commits and CHANGELOG.md are for | `critic` |
| Markdown follows the [Documentation](#documentation) rules: first sentence is the point, short sentences, links instead of repetition | `docs-reviewer` |

## Architecture rules

These keep the modulith a modulith. See [ARCHITECTURE.md](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/main/docs/ARCHITECTURE.md) and the [ADRs](adr/).

| Layer | May import | Must not import |
| --- | --- | --- |
| `heinzel.kernel` | stdlib, pydantic, pluggy, rich (types only) | `heinzel.heads`, `heinzel.modules` |
| `heinzel.modules.*` | `hz` (public API), its own submodules | `heinzel.heads`, other modules' private `_` files |
| `heinzel.heads.*` | `heinzel.kernel`, typer/rich (CLI) | `heinzel.modules` (heads discover, never import) |
| plugins | `hz` (public API only) | `heinzel.kernel._*`, `heinzel.modules.*`, `heinzel.heads.*` |

- A module talks to another module through services injected by type, never through private imports.
- An operation is a thin function: validate input via types, call a service, return a model.
- Services hold the logic and are unit-testable without any head.
- `render=` hints are the only presentation code a module may contain.

## Tests

We follow the test taxonomy of the [Aignostics Python SDK](https://github.com/aignostics/python-sdk) (Martin
Fowler's solitary vs. sociable distinction). Every test carries **exactly one** marker; the suite refuses others.
The `heinzel.testing` pytest plugin enforces the markers and the timeouts below, in Heinzelmännchen and in plugins.

| Marker | What it tests | Collaborators | Network | Timeout |
| --- | --- | --- | --- | --- |
| `unit` | One layer of one module in isolation | Mocked, except shared kernel utilities | Never | 5 s |
| `integration` | Interactions across layers (head → kernel → module → service) or between modules | Real objects, real file I/O, real subprocesses; external services mocked | Never | 15 s |
| `e2e` | Complete workflows as a user runs them (`uv tool install`, `hz system plugin add`) | Everything real | Yes | 120 s, or longer set on the test; ≥ 5 min ⇒ also `long_running` |

- File names end in `_test.py` and mirror `src/`: `tests/unit/kernel/kernel_test.py` tests `src/heinzel/kernel/_kernel.py`.
- Arrange–act–assert, one behaviour per test, names that state the behaviour:
  `test_disabled_modules_and_their_submodules_are_blocked`.
- Assert on outcomes (return values, JSON output, files), not on implementation details.
- Coverage: ≥ 90 % branch coverage for unit + integration. New code should be fully covered; an uncovered line
  needs a reason.
- No sleeping, no ordering dependencies, no shared mutable fixtures. Tests run on macOS and Linux, Python 3.12–3.15.

## Commits, branches and releases

- [Conventional Commits](https://www.conventionalcommits.org): `type(scope): summary` in the imperative; the whole
  first line ≤ 72 characters (a squash merge may append ` (#12)`). Types: `feat`, `fix`, `perf`, `refactor`, `docs`,
  `test`, `build`, `ci`, `chore`, `style`, `revert`. Breaking changes: `!` after the type or a `BREAKING CHANGE:`
  footer. Commitizen's `schema_pattern` in `pyproject.toml` enforces this in the commit-msg hook, `make commits`
  and CI.
- Security fixes use the `security` scope (`fix(security): ...`); commits that resolve a `CVE-…` or `GHSA-…` name it
  in the body. Both land in the changelog's **Security** section. Breaking changes (`!` or a `BREAKING CHANGE:`
  footer) land in **Breaking changes**, with the footer's explanation.
- Scopes are optional; Heinzelmännchen's are `kernel`, `cli`, `heads`, `system`, `workflow`, `plugin`, `template`,
  `ci`, `docs`, `adr`, `agents`, `dx`, `security`, `release`, `deps`, and the first-party plugins `clock` and
  `slack`. A plugin's scopes are its own module and area names.
- One logical change per commit; the body says *why*.
- `main` changes only through pull requests ([ADR 0013](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/main/docs/adr/0013-pull-request-based-development-and-releases.md)).
  They are squash-merged; the PR title must be a conventional commit and becomes the commit on `main`.
- Releases are pull requests too, opened by the Release workflow; merging one tags and publishes it.
- Versions follow [SemVer](https://semver.org) and are derived from commits by git-cliff. While on 0.x, `feat`
  bumps the minor version, everything else the patch version.

## Security

- No secrets in code, tests, fixtures or logs. Configuration comes from `HZ_*` environment variables.
- Dependencies have lower bounds and are locked in `uv.lock`; `make audit` (pip-audit) must pass.
- GitHub Actions are pinned to commit SHAs, run with least-privilege `permissions`, never persist credentials,
  and pass [zizmor](https://docs.zizmor.sh).
- Release artifacts carry build provenance attestations.
- Subprocesses: argument lists only, executables resolved with `shutil.which`, user input never interpolated into a
  shell string.
- See [SECURITY.md](../SECURITY.md) for reporting vulnerabilities.

## Documentation

- [INTENT.md](../INTENT.md) states why Heinzelmännchen exists; change it only with the maintainers.
- Every significant decision is an [ADR](adr/) in MADR format, numbered, never rewritten once accepted — superseded.
- README explains how to use Heinzelmännchen; AGENTS.md how to work on it; this file how to write code for it.
- Docs are part of the change: a pull request that changes behaviour updates the docs in the same commit.
