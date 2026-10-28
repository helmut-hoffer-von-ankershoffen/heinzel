---
status: proposed
date: 2026-10-28
decision-makers: maintainer
amends: 0085
---

# 0093: Touch users inspect updates before installing

## Context and Problem Statement

The footer update control in [ADR 0085](0085-web-updates-verify-the-reloaded-frontend.md) starts the updater on a
click when an update is available. A touch screen has no reliable hover to reveal the update pane first, so a tap
cannot inspect the installed version or date without starting installation.

## Considered Options

- **Inspect first on touch, keep one-click updates for precise pointers** (chosen): touch can see the details and
  choose the update action explicitly, while a mouse can inspect on hover and update with one click.
- **Keep one-click updates on every device**: touch users cannot reliably inspect the pane before installing.

## Decision Outcome

Chosen option: **inspect first on touch, keep one-click updates for precise pointers**, because both types of input
can inspect the details before the updater runs.

- On devices without a fine pointer and hover, tapping the footer control opens the pane; an explicit button inside
  starts the existing verified update flow. On a device with both, hover or focus previews and a click starts it.
- The pane shows the installed version and a best-effort installation date from uv's core metadata. Missing or
  unsupported metadata is shown as unavailable. Copied environments can preserve an older metadata timestamp.
- A deliberately pinned or otherwise unsupported installation is not labeled up to date solely because its version
  equals the cached release. The existing manual-update explanation remains visible.

### Consequences

- Good: an available update's details can be inspected on a phone without starting installation.
- Bad: touch users need a second tap to update.

### Confirmation

`web/src/layout/UpdateStatus.test.tsx` tests the deliberate touch and pointer actions and missing metadata.
`web/e2e/mobile-responsive.mjs` opens the pane and its update action on phone-sized touch viewports.
`tests/integration/web_update_test.py` tests the authenticated date and metadata boundaries.
