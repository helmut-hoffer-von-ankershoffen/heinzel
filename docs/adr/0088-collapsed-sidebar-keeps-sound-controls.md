---
status: proposed
date: 2026-10-27
decision-makers: maintainer
amends: 0058
---

# 0088: The collapsed sidebar keeps sound controls

## Context and Problem Statement

Collapsing navigation currently turns a docked stream into a floating video covering the workspace, especially on
phones. Playback must remain available in the narrow rail without occupying the content area.

## Considered Options

- **Compact rail controls with the mounted stream retained** (chosen): preserves playback and content space.
- **Float the player when navigation collapses**: the current behaviour obscures the workspace on phones.

## Decision Outcome

Chosen option: **compact rail controls with the mounted stream retained**, because playback stays available
without covering the workspace.

The collapsed sidebar offers direct play/pause and the existing sound picker. A docked stream card remains mounted
but hidden while the rail is collapsed. Expanding restores the same iframe and playback position. This amends
ADR 0058's always-visible card requirement for this explicit compact navigation state. A deliberately floating
player remains floating. No stream is contacted before a Play gesture.

### Consequences

The rail controls and full player share the same playback state. Collapse and expand never remount the iframe.
Regression tests cover playback controls, iframe continuity and the explicit floating preference.

### Confirmation

`web/src/sound/Dock.test.tsx` checks compact play/pause, first play, iframe continuity and floating preference.
