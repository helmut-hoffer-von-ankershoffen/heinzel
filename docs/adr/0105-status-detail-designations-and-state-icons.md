---
status: proposed
date: 2026-10-30
implementation: built
related: 0056
implemented-by: 292
---

# 0105: Status detail rows carry provider designations and work state icons

## Context and Problem Statement

Clock detail rows lose the primary designation and activity icons shown in the plugin panel.
Sidebar detail rows also discard supporting text already present in the public contribution data.

## Considered Options

- **Extend the existing detail data and render it consistently** (chosen).
- Parse clock labels in core: would couple the host to plugin semantics.

## Decision Outcome

Chosen option: **extend the existing detail data and render it consistently**, because providers own meaning
while the host owns layout.

`DetailLine.badge` is optional bounded text rendered beside the label in footer and sidebar details.
The closed icon vocabulary adds `briefcase-business`, `moon` and `party-popper`.
Sidebar details preserve secondary text, live values, icons and tooltips just as footer details do.

### Consequences

- Existing providers remain compatible; new providers can supply a short designation.
- Providers using the added fields require a compatible core release.

### Confirmation

Contribution component tests check both detail surfaces. The exhaustive icon map is checked by TypeScript.
Clock plugin tests check provider state selection independently.
