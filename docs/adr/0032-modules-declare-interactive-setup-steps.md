---
status: proposed
date: 2026-10-24
decision-makers: maintainer
amends: 0018
related: 0003, 0005, 0012, 0015, 0024, 0026, 0030
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
- **Commands (CLI only).** `hz setup [MODULE]` is the command that runs setup steps (`hz join` runs the missing ones
  too, see *One journey*): `hz setup` runs every step in order (see *Order*); `hz setup clock` runs one module's step,
  without the skip question. A name without a step that can run is a usage error that says why and lists the
  modules with one. An optional step asks "Set up clock now? [Y/n]" first; a required one doesn't. A step whose
  module is already configured says so and asks whether to change it. Ctrl-C stops the run and keeps finished steps;
  configuration files are replaced whole, never half written. A step that fails is reported and the others still
  run. `hz setup` ends with a summary and `hz system doctor` on the configuration just
  written, and exits 1 when doctor reports a check down, saying so on stderr. A setup step never changes a module's
  command path: the module keeps exactly the commands it has without one (`hz clock`, `hz echo hi`), as in MCP and
  REST.
- **Update notice** ([ADR 0031](0031-update-check.md)). `hz setup clock` is followed by the notice like every other
  command. `hz setup` leaves it to the doctor it ends with, whose `release` check names a newer release; when doctor
  doesn't run (disabled, or the run was stopped with Ctrl-C) the notice follows instead.
- **Already configured.** A module counts as configured when one of its settings is set in a file or the environment,
  or one of its declared credentials is stored. A module with neither always runs its step.
- **No terminal.** Setup asks its questions on standard output and reads the answers from standard input, so both
  must be a terminal: `hz setup > out.txt` would otherwise wait for answers to questions nobody sees. Otherwise,
  unless an answers file is given (*Answers*), it refuses and prints, on stderr, the equivalent
  `hz system config set` and `hz system credential set` commands for each step's module.
- **Order.** `@module.setup(phase=...)` places a step: `identity` (who you are), `membership` (whom you work with),
  `connect` (tools, credentials, bindings; the default), `work` (what hz starts doing for you) and `run` (whether it
  runs on its own). Steps run phase by phase, built-in modules first inside a phase, then the others alphabetically.
  Phases only order the steps: no heading, prompt or document shows them to a person.
- **One journey, joined at the front door.** `hz join <team repository>` (the perfect-onboarding ADR) is how a person
  starts: it says what hz keeps about them, runs the `identity` steps, joins the team, then runs the steps still
  missing. `hz setup` is what they re-run any time for whatever is still missing. Both say what hz keeps, and where,
  before they ask anything.
- **Resumable.** Every finished step is recorded, with when and its keyed answers, in `<state_dir>/setup.json`
  (mode 0600, written after each step). `hz setup` runs only the steps no earlier run finished (never run, failed or
  interrupted), says which are done, and, when nothing is missing, says so and runs doctor; `--restart` forgets the
  record and goes through every step again.
- **Answers.** A question may carry a `key`, its name within the module (`role` asked by `org` is `org.role`).
  `--answers <file>` is TOML with a table per module (`[org] role = "engineer"`): keyed questions take their answer
  from it, an invalid one fails instead of being asked again, and with no terminal a keyed question without an
  answer takes its default or, without one, fails naming the key. Without a terminal, a module with answers runs its
  step; one without keeps its configuration, an optional step is skipped and a required one runs. `--print-answers`
  writes the recorded answers as such a file, so a team bootstraps its answers from one finished setup;
  `--json` prints every step's outcome and doctor's status, questions going to stderr. Secrets are never keyed, so
  they never come from or go to a file ([ADR 0030](0030-credentials-live-in-the-os-keychain.md)).
- **Names.** `setup` and `join` are reserved as module names, at every level, because `hz setup` and `hz join` are
  top-level commands (and `[join]` is the answers file's table for `hz join`'s own questions). A module's operations
  may take any name, `setup` included: the step is never one of the module's commands.
- **Top level.** `hz setup` and `hz join` are exceptions to ADR 0018, decided by the maintainer: everything else
  about Heinzelmännchen itself stays under `hz system`.
- **Nothing without a yes.** Without a person, `hz join` changes nothing unless the yes was given in advance:
  `--yes` (or `[join] yes = true`) makes a team whose roster lists you yours, and `--propose` (`[join] propose =
  true`) separately allows the pull request that asks its owners to add you. A missing yes fails naming the flag. The
  read-only clone that reads the roster happens before any yes. With a person, an answers file that would change a
  module already set up asks first; flags the person typed don't.

### Consequences

- Good: a new user runs `hz setup` once; a plugin author adds a wizard in a few lines and tests it with scripted
  answers.
- Good: the wizard can't write what `hz system config set` would refuse; MCP and REST never see setup.
- Good: a coding agent or a team sets someone up without a terminal from an answers file, and a run that was stopped
  continues where it stopped.
- Bad: the order is coarse (phase, built-ins, then alphabetical). A step that needs another step of the same phase
  first has to say so in its text until an `after=` declaration exists.
- Bad: the record in `setup.json` can disagree with the configuration when someone edits the files by hand; the
  configured heuristic still offers a change, and `--restart` resets the record.
- Bad: `hz setup` exits 1 when any doctor check is down, also one of a module whose step was skipped: the run did
  what was asked, but the installation isn't healthy yet, and doctor's table says which check to fix.
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
- `tests/unit/kernel/prompter_test.py` and `tests/unit/heads/cli_journey_test.py`: keyed questions and preset
  answers, defaults without a person, answers files and the record in `setup.json`;
  `tests/integration/journey_test.py`: the order with who you are first, the privacy line first, resuming and
  `--restart`, `--answers` without a terminal, `--print-answers`, `--json`, and `hz join` found in the roster,
  proposed, and run by a coding agent with flags only.
- `tests/unit/public_api_test.py` snapshots the new names.

## More Information

- Decided by the maintainer: setup steps declared by modules, required or optional, one module's step on its own
  and a top-level `hz setup`, 2026-10-24.
  The technical shape (let, prompter, configured heuristic, order) was delegated.
- Amended on 2026-10-24, decided by the maintainer: adding a setup step must never change a module's command path.
  The first version mounted the step as `hz <module> setup`, which turned a module that collapses into one command
  into a group; when that command took an argument it no longer collapsed (`hz echo hi` became "No such command"),
  so the CLI path differed from the MCP and REST ones, and a plugin release adding a step broke scripts that called
  the module. `hz setup [MODULE]` is now the single entry for setup, and modules keep exactly their commands.
  Command paths stay stable across heads, and scripts don't break when a plugin adds a step.
- Amended on 2026-10-24 for onboarding (the perfect-onboarding ADR, proposed in pull request #32): steps declare a
  phase that orders them, the run is recorded and resumable, keyed questions can be answered from a file, and
  `hz join` is the front door with `hz setup` as the re-runnable "what is still missing". Decided by the maintainer:
  phases stay internal ordering, never shown; the person's story is the invite line.
- Possible extensions: `after=` to order steps by dependency.
