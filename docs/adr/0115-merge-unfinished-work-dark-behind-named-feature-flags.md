---
status: accepted
date: 2026-11-01
implementation: built
decision-makers: maintainer
implemented-by: 354
related: 0002
---

# 0115: Merge unfinished work dark behind named feature flags

## Context and Problem Statement

Big work, such as durable agents, takes many pull requests. Merging each into `main` as it is ready keeps branches
short, but half-built behaviour must not reach users. The only switch was `disabled_modules`, which turns off a whole
module. How do we merge unfinished work and keep it invisible until it is done?

## Decision Drivers

- Every green merge is released, so anything on `main` can reach users.
- A typo in a flag name must not leave work dark forever.
- Flags are temporary: they should be easy to find and delete.

## Considered Options

- **A registry of named flags, off by default** (chosen)
- **Long-lived feature branches**: they drift from `main` and conflict late.
- **`disabled_modules` alone**: too coarse for work inside a module.

## Decision Outcome

Chosen option: **a registry of named flags, off by default**.

- `src/heinzel/kernel/_features.py` lists every flag with a one-line reason; all are off by default.
- The expert setting `features` (environment `HZ_FEATURES`) switches flags on by name.
- `feature_enabled(name)` raises on a name that is not registered.
- A flag is deleted with its checks when the work ships. A flag hides behaviour, never state schema changes.

### Consequences

- Good: unfinished work merges early and is released dark.
- Bad: each flag is code to remove later; configured names with no flag are not yet shown to the user.

### Confirmation

`tests/unit/kernel/features_test.py` fails when an unregistered name is checked or a flag defaults on.
