---
status: accepted
date: 2026-10-23
---

# 0002: A modulith of a kernel, modules and lets

## Context and Problem Statement

Heinzelmännchen must be extensible by teams who never touch the core, composable from small pieces, and usable by
people and agents through several interfaces. A monolithic command tree would couple every feature to the CLI and to
each other.

## Considered Options

- **One process: a small kernel, modules and typed lets** (chosen)
- **Typer sub-apps registered by hand** (the common pattern): couples features to the CLI, no other heads.
- **Subclass discovery by scanning for base classes** (as in the Aignostics SDK): implicit, import-order sensitive,
  and hard to reason about for agents; explicit declarations are easier to read and to lint.
- **Microservices**: operational overhead without benefit at this scale.

## Decision Outcome

Chosen option: **a modulith of a kernel, modules and lets**, because features stay decoupled from each other and
from any one interface.

- Heinzelmännchen is one process with a small **kernel** and many **modules**.
- A module is a package declaring `module = Module(name, summary)`; nested declaring packages are submodules.
- Modules advertise typed **lets** (`OperationLet`, `ServiceLet`, `InfoLet`) to the kernel. The kernel stores them
  without interpreting them and hands them out by kind.
- Built-in modules and plugin modules are treated identically.

### Consequences

- Good: adding a feature never edits a central registry; adding a kind of let never edits existing modules.
- Good: module boundaries are explicit and lintable (import-linter contracts).
- Bad: the kernel is the only part every module depends on, so its API is versioned with care; it must stay small
  and generic.

### Confirmation

import-linter contracts in `pyproject.toml` (`make lint`, `contracts` step): "The kernel knows nothing about heads
or modules" and "Modules use only the kernel's public API, like plugins". `tests/unit/kernel/kernel_test.py`
(`test_lets_by_kind_with_owner`) and `tests/unit/kernel/module_test.py` cover lets.
