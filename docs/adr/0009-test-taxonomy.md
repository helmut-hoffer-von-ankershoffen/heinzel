---
status: accepted
date: 2026-10-23
---

# 0009: Unit, integration and e2e tests, as in the Aignostics Python SDK

## Context and Problem Statement

Without precise definitions, "integration test" means anything, suites get slow and flaky, and offline development
breaks.

## Considered Options

- **The taxonomy of the [Aignostics Python SDK](https://github.com/aignostics/python-sdk)** (chosen)
- **No precise definitions** (the situation described above).

No other alternatives were recorded when this decision was made.

## Decision Outcome

Chosen option: **adopt the SDK's taxonomy** (Fowler's solitary vs. sociable tests), because each level then has one
meaning and offline runs stay fast.

- `unit`: isolated, mocked, offline.
- `integration`: real collaborators across layers, real file I/O and subprocesses, offline.
- `e2e`: real external services.
- Every test carries exactly one marker; the suite refuses to run otherwise. Definitions live
  in [CODING_STANDARDS.md](../CODING_STANDARDS.md#tests).

### Consequences

- Good: `make check` runs unit + integration offline in seconds; e2e runs in CI on macOS and Linux.
- Neutral: reviewers (and the `test-engineer` agent) check that each test sits at the right level.

### Confirmation

The `heinzel.testing` pytest plugin fails collection for any test without exactly one of the markers `unit`,
`integration`, `e2e`, here and in plugins;
the `test-engineer` agent checks the level in review.
