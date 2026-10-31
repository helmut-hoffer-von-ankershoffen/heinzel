---
status: proposed
date: 2026-10-24
decision-makers: maintainer
amends: 0018, 0039
amends-0018: adds hz system expert; help lists the everyday commands
amends-0039: the navigation lists the same everyday commands
related: 0003, 0012, 0019, 0028, 0032
amended-by: 0051
implemented-by: 65
---

# 0041: Expert mode: newcomers see the everyday commands, `hz system expert on` shows all

## Context and Problem Statement

`hz --help` lists eleven groups and about sixty commands: configuration, credentials, capability bindings, the
daemon, servers, plugins, contract graphs, pruning. A person who just joined a team needs a dozen of them. The
maintainer asked for "an expert mode. by default hz is not in expert mode. expert mode is a setting. there is a
command to enable expert mode via the cli, and leave it again via cli." Read as: by default most commands are hidden
so newcomers see a small, friendly surface, and expert mode shows everything. The questions are what decides who a
command is for, what "hidden" means in each head, and where the command lives.

## Decision Drivers

- Declared beats discovered by convention (coding standards): whether a command is everyday is said where the
  operation is declared, not kept in a list in a head.
- Hidden must not mean disabled: a guide, a colleague or an old note that names an expert command must still work,
  and `--help` on it must still answer.
- Heads are views (ADR 0003): the web app shows the same split as the CLI and gets it from an operation.
- Agents are first-class users: machine heads are not cluttered by what a person browses, so they keep everything.

## Considered Options

- **An `audience` on every operation, `expert` by default; a user setting `[hz] expert`; `hz system expert`
  [`on`|`off`]** (chosen)
- **A list of everyday commands in the CLI head**: a second source of truth beside the operations, invisible to
  plugins and to the web head.
- **`everyday` by default, `expert=True` on the rest**: about fifty markers instead of fifteen, and every plugin
  that never heard of audiences lands in the newcomer's help.
- **Disable expert commands outside expert mode**: breaks every script, guide and agent that calls one, for a
  problem that is only about what is listed.
- **`hz expert` at the top level**: shorter, but ADR 0018 keeps the top level for work; `hz setup`, `hz join` and
  `hz ui` are top-level by the maintainer's explicit decision, and none was given for this. A one-line change if the
  maintainer wants it.

## Decision Outcome

Chosen option: **an `audience` on every operation and a user setting**, because the declaration sits with the
operation, every head reads the same field, and nothing stops working.

- `@module.operation(audience="everyday")` declares an everyday operation; `audience="expert"` is the default
  (`OperationLet.audience`). Plugins choose for their own operations; the first-party `clock` is everyday.
- The everyday set: `hz join`, `hz setup`, `hz ui` (commands of the CLI head), `hz team new | join | add | show`,
  `hz org show | sync`, `hz workflow list | show | new | drafts | run`, `hz system doctor | update | expert [on|off]`,
  `hz clock` when installed, and `hz --version`. Everything else is for experts.
- `[hz] expert = false` is a kernel setting, user scope only: a project `hz.toml` can't set it
  (`ignored_in_project`), because a repository someone else controls must not decide what a person's tool shows.
  `HZ_EXPERT=1` turns it on for one shell or one agent.
- `hz system expert` shows the mode; `hz system expert on` and `off` write the user configuration file through
  `ConfigEditor` and answer with the mode in effect, read from the files and the environment at every call (a running
  `hz ui` sees a switch made in a terminal); when `HZ_EXPERT` or the `HZ_CONFIG` file decides, the answer says which. It is the submodule
  `system.expert` (ADR 0018: it is about Heinzelmännchen itself).
- A module's operation named like the module, beside other operations, is what the module's path does by itself:
  `hz system expert` runs it, `GET /system/expert` and the MCP tool `system_expert` are it, next to
  `hz system expert on`, provided every parameter it takes has a default (a required one would swallow a
  subcommand's name, so such an operation keeps its own name). Before, only a module whose only operation it was
  collapsed into it.
- Hidden, in the CLI head outside expert mode: expert commands, and groups holding nothing else, are left out of
  `--help` and shell completion. They run when typed, their `--help` works, and inside a hidden group everything is
  listed. `hz system serve` is for experts. `hz` and `hz --help` end with one line: how many commands are hidden and
  `hz system expert on`; so does the help of every group that hides some of its commands.
- The web head shows the same split: every screen names the operation it shows; the navigation and the search list
  a screen when its operation is everyday or expert mode is on, reading both from `GET /api/system/expert`
  (`expert` and every operation's audience). Settings has a switch that calls `POST /api/system/expert/on` or
  `off`. A screen that isn't listed still opens by its URL. This amends ADR 0039's navigation.
- MCP and REST list every operation whatever the mode: an agent or a script is configured once and reads tool
  schemas, not menus, and hiding tools would only make it guess.
- The generated docs list everything: `docs/CLI_REFERENCE.md` marks expert commands `(expert)`, `docs/MODULES.md`
  has an Expert column.

### Consequences

- Good: a newcomer's `hz --help` shows about twenty commands; the rest is one line away.
- Good: an operation says who it is for in one place, and every head follows.
- Bad: plugins written before this release show only in expert mode until they declare `audience="everyday"`; the
  plugin template adds it to its example once its Heinzelmännchen version includes this release.
- Bad: guides and tutorials must use everyday commands or say "with `hz system expert on`".
- Neutral: the help of `hz system` lists three commands outside expert mode.

### Confirmation

- `tests/integration/cli_test.py`: default help lists only everyday commands and ends with the hint; hidden commands
  run and answer `--help`; shell completion offers expert commands only in expert mode; `hz system expert on|off`
  write the user setting; a project file can't turn it on; the generated reference marks expert commands.
- `tests/integration/expert_test.py`: the web head reads and switches the mode through the API; MCP, REST and web
  list expert operations outside expert mode.
- `tests/unit/kernel/module_test.py` (the default and validation of `audience`),
  `tests/unit/heads/exposure_test.py` (an operation named like its module takes the module's path), and Vitest:
  `web/src/layout/navigation.test.ts`, `Shell.test.tsx`, `Settings.test.tsx`.
- Review: a new operation chooses its audience; `everyday` needs a newcomer's reason.

## More Information

- Requested by the maintainer, 2026-10-24; the interpretation (most operations hidden by default, expert mode shows
  everything) was stated to the maintainer, who may correct it.
- Open: whether `hz system expert` should move to the top level as `hz expert`; whether a plugin may set a default
  audience for all its operations at once.
