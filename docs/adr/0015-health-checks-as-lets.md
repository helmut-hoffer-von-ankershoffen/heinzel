---
status: accepted
date: 2026-10-23
---

# 0015: Health checks are lets, aggregated by `hz system doctor`

## Context and Problem Statement

People, agents and CI need one question answered quickly: is this installation healthy, and if not, what do I do?
Every module (and plugin) knows best what healthy means for it. The Aignostics Python SDK has a similar idea: every
service implements `health()`, aggregated by a system command.

## Considered Options

- **Health checks as a let kind, aggregated by `hz system doctor`** (chosen)

No alternatives were recorded when this decision was made.

## Decision Outcome

Chosen option: **health checks are lets**, because each module knows what healthy means for it and one command
aggregates them.

- A module advertises checks with `@module.health`, a provider returning a list of
  `Check(name, status, detail, fix)`. The provider's parameters are injected like an operation's.
- `Status` is `up`, `degraded` or `down`; anything but `up` must carry a `detail`, and should carry a `fix`.
- `hz system doctor` runs every provider; a provider that raises becomes a `down` check instead of a crash. The
  overall status is the worst one (down beats degraded beats up), and the command exits with 1 when it is `down`.
- Heads may map results to exit codes through an operation's `exit_code` hint.

### Consequences

- Good: bug reports and agents start from `hz system doctor --json`.
- Neutral: a new let kind, consumed by a built-in module rather than a head.

### Confirmation

`tests/unit/kernel/kernel_test.py::test_checks_must_explain_problems` and `tests/integration/cli_test.py`
(`test_doctor_reports_and_exits_by_status`, `test_doctor_is_down_when_a_check_fails`).
