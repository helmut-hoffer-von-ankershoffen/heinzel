---
status: accepted
date: 2026-10-23
implementation: built
---

# 0003: Heads are views on head-agnostic operations

## Context and Problem Statement

Heinzelmännchen will be used from a terminal, by coding agents through MCP, by other systems through HTTP, and
proactively by a daemon. Writing each feature once per interface multiplies code and drift.

## Considered Options

- **Plain typed operations; heads derive their interface from the signature** (chosen)
- **Typer-first commands with MCP wrappers**: Typer types leak into the business logic.
- **Separate function sets per head**: duplicated behaviour and docs.

## Decision Outcome

Chosen option: **heads are views on head-agnostic operations**, because a feature is written once and appears in
every head.

- An operation is a plain typed function: parameters annotated as `Annotated[T, Field(description=...)]`, a
  docstring whose first line is its summary, a JSON-serializable return value (usually a pydantic model).
- Heads derive their interface from that signature. The CLI head ships first; MCP, REST and daemon heads follow the
  same recipe.
- The only presentation code a module may contain is an optional `render=` hint for human-facing heads.
- Failures meant for users are `HeinzelError`; heads map them to their own error shape.

### Consequences

- Good: every CLI command has `--json` for free, and every operation will be an MCP tool for free.
- Bad: operations must not print or prompt. Interactive flows (like `hz system plugin new`, which runs copier) are
  the exception and also accept all their answers as arguments, so agents can run them unattended.
- Neutral: `read_only=False` marks operations that change state.

### Confirmation

- `tests/unit/documentation_test.py::test_every_parameter_setting_and_module_is_described` and
  `tests/unit/kernel/module_test.py::test_operation_requires_docstring`.
- `tests/unit/heads/cli_command_test.py::test_command_hides_injected_parameters_and_adds_json`.
- ruff (`select = ["ALL"]`, including `T201`) rejects `print`; import-linter's "Modules never import heads".
