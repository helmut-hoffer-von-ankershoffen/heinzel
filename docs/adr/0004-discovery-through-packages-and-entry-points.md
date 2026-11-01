---
status: accepted
date: 2026-10-23
implementation: built
---

# 0004: Discovery through packages, entry points and pluggy

## Context and Problem Statement

Modules must be found without manual registration, both inside Heinzelmännchen and in independently installed
plugins, and a broken plugin must never break Heinzelmännchen.

## Considered Options

- **Walk declaring packages and `heinzel.modules` entry points; register with pluggy** (chosen)
- **One entry point per command**: ties plugins to the CLI head.
- **Scanning `sys.modules` for subclasses**: implicit and order-dependent.

## Decision Outcome

Chosen option: **discovery through packages, entry points and pluggy**, because built-ins and plugins share one
mechanism and pluggy is already familiar to plugin authors.

- The kernel walks `heinzel.modules` and every package named by an entry point in the `heinzel.modules` group. A
  declaring package is a module; its qualified name follows the nearest declaring ancestor. Packages starting with
  `_` are skipped.
- Modules are registered with a [pluggy](https://pluggy.readthedocs.io) plugin manager under their qualified name;
  lets flow through the `heinzel_lets` hook. `HZ_DISABLED_MODULES` blocks modules and their submodules;
  `HZ_PLUGINS_ENABLED=false` skips entry points entirely.
- Plugin import errors are collected and shown by `hz system info`; built-in errors raise, because they are bugs.

### Consequences

- Good: one mechanism for built-ins and plugins. Plugins can have submodules too.
- Good: pluggy gives us blocking, introspection and room for lifecycle hooks without inventing a plugin manager.
  pytest and [Datasette](https://docs.datasette.io/en/stable/plugins.html) use the same combination (pluggy + entry
  points), which keeps Heinzelmännchen familiar to plugin authors.

### Confirmation

`tests/integration/discovery_test.py` (`test_walks_nested_packages_and_skips_private_ones`),
`tests/unit/kernel/discovery_test.py` (`test_broken_plugins_are_reported_not_raised`, `test_plugins_can_be_skipped`)
and `tests/unit/kernel/kernel_test.py::test_disabled_modules_and_their_submodules_are_blocked`.
