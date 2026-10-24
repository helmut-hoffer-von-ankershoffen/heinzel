---
status: proposed
date: 2026-10-24
decision-makers: maintainer
amends: 0018
related: 0003, 0005, 0012, 0015, 0026, 0030
---

# 0032: Modules declare interactive setup steps; `hz setup` runs them all

## Context and Problem Statement

Configuring a module today means reading [CONFIGURATION.md](../CONFIGURATION.md) and typing
`hz system config set clock.zones '[...]'` or `hz system credential set ...`, with the TOML syntax right the first
time. The maintainer asked for more: "modules can contribute to an interactive setup, so one can e.g. do hz clock
setup, which then interactively asks for timezone and working hours per timezone [...] wizard style in the cli. when
the user does hz setup, this triggers the setup steps of all modules. setup steps can be required or optional, which
is something the module can declare. on optional, the user can skip the setup step on hz setup". How does a module
declare such a step, how does it ask questions without knowing about the terminal
([ADR 0003](0003-heads-are-views-on-operations.md)), and where do the commands live, given that
[ADR 0018](0018-one-administrative-namespace.md) keeps the top level free of administration?

## Decision Drivers

- Modules declare, the kernel collects, a head consumes: the pattern of health checks
  ([ADR 0015](0015-health-checks-as-lets.md)) and credentials ([ADR 0030](0030-credentials-live-in-the-os-keychain.md)).
- A step writes through the same validated paths as the commands people already use, so the wizard can't write a
  value `hz system config set` would refuse.
- Testable without a terminal: a step is a function, its questions can be scripted.
- Only an interactive head can run a wizard; MCP and REST clients are not people at a keyboard.

## Considered Options

- **A `SetupLet` declared with `@module.setup(required=...)`, asking through an injected `Prompter`, run by the CLI
  head** (chosen)
- **A setup operation per module** (`@module.operation(heads={"cli"})` named `setup`): an operation is a single call
  with parameters; MCP and REST would have to be excluded by hand, and `hz setup` would have to guess which
  operations are setup steps by their name.
- **A generic step derived from each settings model** (one question per field): asks things nobody needs to answer
  (directories, log levels), can't search IANA names or ask "hours per zone", and the maintainer wants modules to
  declare their steps. Rejected; a module without a declared step has none.
- **`hz system setup` instead of `hz setup`**: consistent with ADR 0018, but the maintainer decided on `hz setup`; it
  is meant to be the first command a new user types.

## Decision Outcome

Chosen option: **a `SetupLet` declared with `@module.setup`, asking through an injected `Prompter`, run by the CLI
head**, because modules stay head-agnostic, steps write through the existing services, and tests script the answers.

- **Declaration.** `@module.setup(required=False)` on a function with a docstring adds a `SetupLet` named after the
  module (one per module, like `InfoLet`). Its first docstring line is shown before the step. Its parameters are
  injected like an operation's: settings, services, `Credentials`, and `Prompter`.
- **Prompter.** `heinzel.Prompter` asks `text`, `choice`, `choices` (several, or none), `confirm`, `number` and `secret`
  questions, each with a default and, where it makes sense, a `validate` callback returning a problem or None; an
  invalid answer is explained and asked again. Heads implement two primitives, `read` and `say`. The kernel's default
  is not interactive and raises `HeinzelError`; the CLI binds a console prompter; tests use
  `heinzel.kernel.ScriptedPrompter` (fixture `hz_prompter`).
- **Writing.** A step writes settings through `heinzel.ConfigEditor` (provided by `system.config`, the same validation
  as `hz system config set`, user scope by default) and secrets through `Credentials.set`. A step that is
  interrupted keeps what it already wrote.
- **Commands (CLI only).** `hz setup [MODULE]` is the one command that runs setup steps: `hz setup` runs every step,
  built-in modules first, then the others alphabetically by qualified name; `hz setup clock` runs one module's step,
  without the skip question. A name without a step that can run is a usage error that lists the modules with one. An optional step asks "Set up clock now? [Y/n]" first; a
  required one doesn't. A step whose module is already configured says so and asks whether to change it. Ctrl-C stops
  the run and keeps finished steps; configuration files are replaced whole, never half written. A step that fails is
  reported and the others still run. `hz setup` ends with a summary and `hz system doctor` on the configuration just
  written, and exits 1 when doctor reports a check down, saying so on stderr. A setup step never changes a module's
  command path: the module keeps exactly the commands it has without one (`hz clock`, `hz echo hi`), as in MCP and
  REST.
- **Update notice** ([ADR 0031](0031-update-check.md)). `hz setup clock` is followed by the notice like every other
  command. `hz setup` leaves it to the doctor it ends with, whose `release` check names a newer release; when doctor
  doesn't run (disabled, or the run was stopped with Ctrl-C) the notice follows instead.
- **Already configured.** A module counts as configured when one of its settings is set in a file or the environment,
  or one of its declared credentials is stored. A module with neither always runs its step.
- **No terminal.** Setup asks its questions on standard output and reads the answers from standard input, so both
  must be a terminal: `hz setup > out.txt` would otherwise wait for answers to questions nobody sees. Otherwise it
  refuses and prints, on stderr, the equivalent `hz system config set` and `hz system credential set` commands for
  each step's module.
- **Names.** `setup` is reserved as a module name, because `hz setup` is a top-level command. A module's operations
  and submodules may take any name, `setup` included: the step is never one of the module's commands.
- **Top level.** `hz setup` is an exception to ADR 0018, decided by the maintainer: everything else about
  Heinzelmännchen itself stays under `hz system`.

### Consequences

- Good: a new user runs `hz setup` once; a plugin author adds a wizard in a few lines and tests it with scripted
  answers.
- Good: the wizard can't write what `hz system config set` would refuse; MCP and REST never see setup.
- Bad: the order is fixed (built-ins, then alphabetical). A step that needs another module's step first has to say so
  in its text until an `after=` declaration exists.
- Bad: "configured" is a heuristic; a module whose defaults are fine is offered its step until something is set.
- Neutral: new public API, additive ([ADR 0026](0026-plugin-api-versioned-with-semver.md)): `SetupLet`,
  `Prompter` and `ConfigEditor` in `heinzel`; for tests `ScriptedPrompter`, `ScriptExhausted` and `INTERRUPT` in
  `heinzel.kernel` and the `hz_prompter` fixture in `heinzel.testing`; `toml_value` in `heinzel.kernel`. All are in
  the snapshot of `tests/unit/public_api_test.py`.

### Confirmation

- `tests/unit/kernel/module_test.py`: the decorator, one step per module, the reserved name `setup`;
  `tests/unit/kernel/prompter_test.py`: every question, its validation and re-asking, with `ScriptedPrompter`.
- `tests/unit/heads/cli_setup_test.py`: the order, the configured heuristic, the equivalent commands, skipping,
  required steps, already configured, failing steps, Ctrl-C and the console prompter.
- `tests/integration/setup_test.py`: `hz setup` and `hz setup <module>` answered on stdin, the summary and doctor
  on the configuration just written, the exit code when doctor reports a check down, the refusal without a terminal,
  that MCP and REST expose no setup, and that a collapsed module whose operation takes an argument and declares a
  step still runs as `hz echo hi`; `tests/unit/heads/cli_setup_test.py`: a terminal is needed on stdin and stdout.
- `tests/integration/cli_test.py`: every operation's command, and every group that runs one, accepts `--json`; a
  collapsed module with a setup step keeps its exit codes. `tests/integration/update_notice_test.py`: exactly one
  notice after its text output and none with `--json`, the notice after `hz setup <module>`, and doctor's instead
  after `hz setup`.
- `plugins/heinzel-clock/tests/clock_test.py`: `hz setup clock` and the migration of the old settings shape.
- `tests/unit/public_api_test.py` snapshots the new names.

## More Information

- Decided by the maintainer: setup steps declared by modules, required or optional, one module's step on its own
  and a top-level `hz setup`, 2026-10-24. The technical shape (let, prompter, configured heuristic, order) was delegated.
- Amended on 2026-10-24, decided by the maintainer: adding a setup step must never change a module's command path.
  The first version mounted the step as `hz <module> setup`, which turned a module that collapses into one command
  into a group; when that command took an argument it no longer collapsed (`hz echo hi` became "No such command"),
  so the CLI path differed from the MCP and REST ones, and a plugin release adding a step broke scripts that called
  the module. `hz setup [MODULE]` is now the single entry for setup, and modules keep exactly their commands.
  Command paths stay stable across heads, and scripts don't break when a plugin adds a step.
- Possible extensions: `after=` to order steps by dependency.
