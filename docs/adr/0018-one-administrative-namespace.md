---
status: accepted
date: 2026-10-23
implementation: built
amended-by: 0032, 0039, 0041, 0045, 0080
---

# 0018: One administrative namespace: `hz system`; skills live in `agent-skills/`

## Context and Problem Statement

The top level of the command tree is the most valuable space in the CLI. It should belong to the team's work —
`hz status`, `hz report weekly`, `hz suggest`, and every module plugins add — not to tooling chores. And "plugin"
must mean one thing in this repository.

## Considered Options

- **Everything about Heinzelmännchen itself under `hz system`** (chosen)
- **Administrative commands at the top level**: they would take the space that belongs to the team's work.

No other alternatives were recorded when this decision was made.

## Decision Outcome

Chosen option: **one administrative namespace, `hz system`**, because the top level stays free for work and
"plugin" keeps one meaning.

- Everything about Heinzelmännchen itself lives under `hz system`: `info`, `modules`, `doctor`, `changelog`,
  `update`, and the submodules `config`, `plugin` and `agent`. Every level reads noun → verb, singular nouns,
  sorted alphabetically.
- The kernel is the core and has no commands. `system` is the only module shipped with the core distribution and
  is essential; every other module, built-in or not, is built like a plugin (import-linter enforces it).
- The Agent Skills (`hz-init`, `hz`, `hz-uninstall`) live in `agent-skills/`, which is also the Claude Code plugin
  the marketplace in `.claude-plugin/` points at.

### Consequences

- Good: a rule for people and agents: `hz system …` maintains the tool, everything else does work.
- Neutral: settings of system submodules live in nested tables: `[system.agent]`, `HZ_SYSTEM_AGENT__*`.
- Neutral: decided before the first release, so no released command moves.

### Confirmation

import-linter contracts "Every module could be a plugin: system's submodules don't import each other" and "Modules
use only the kernel's public API, like plugins"; `tests/unit/kernel/kernel_test.py::test_essential_modules_cannot_be_disabled`,
`tests/integration/cli_test.py::test_commands_are_sorted` and
`tests/unit/repository_test.py::test_claude_plugin_marketplace_points_at_the_plugin`.
