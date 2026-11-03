---
status: proposed
date: 2026-11-03
implementation: built
decision-makers: maintainer
related: 0056, 0094
---

# 0118: Contributions declare footer retention priority

## Context and Problem Statement

A narrow footer cannot show every status contribution. The host needs a generic way to decide what to move into More,
without recognizing individual plugin identities or overriding the person's choice of slots.

## Considered Options

- **A bounded priority on each status item** (chosen): any provider can express the relative value of retaining it.
- **A named host exception for each important plugin**: this couples core to individual plugin features.
- **Always retain every item**: finite viewports cannot fit an unbounded number of controls.

## Decision Outcome

Chosen option: **a bounded priority on each status item**, because it lets the host fit generic contributions without
knowing their providers.

- `footer_priority` is an integer from 0 to 1000, defaulting to 40 for existing contributions. A higher effective
  priority is considered first; the host adds its existing warning and error boosts.
- Equal effective priorities are considered in locale-independent contribution ID order. Displayed items retain
  their declared visual order. Items that do not fit remain available in More with their state and actions.
- Priority is not a visibility guarantee: competing items and very narrow viewports can leave insufficient room.
  An explicitly hidden contribution remains hidden. A plugin using the field requires a core release that supports it.

### Consequences

- Good: plugins use a shared contract instead of host exceptions and old contributions keep their existing priority.
- Bad: providers must choose priorities responsibly and releases must put compatible core before a plugin using the field.

### Confirmation

Contract tests reject invalid values and cover defaults. Layout tests exercise tie-breaking and browser tests verify
retained and overflowed contributions, warning states and actions on narrow viewports.
