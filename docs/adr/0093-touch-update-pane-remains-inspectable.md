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

- **Inspect first, then choose the update action** (chosen): every input method can see the details before installing.
- **Keep one-click updates on every device**: touch users cannot reliably inspect the pane before installing.
- **Switch by pointer capability**: hybrid devices can have both touch and hover, so a tap could still install immediately.

## Decision Outcome

Chosen option: **inspect first, then choose the update action**, because all input methods can inspect the details
before the updater runs, including on hybrid devices.

- Activating the footer control opens the pane; an explicit button inside starts the existing verified update flow.
  Hover and focus continue to preview the pane.
- The pane shows the installed version and a best-effort installation date from uv's core metadata. Missing or
  unsupported metadata is shown as unavailable. Copied environments can preserve an older metadata timestamp.
- A deliberately pinned or otherwise unsupported installation is not labeled up to date solely because its version
  equals the cached release. The existing manual-update explanation remains visible.

### Consequences

- Good: an available update's details can be inspected on a phone without starting installation.
- Bad: starting an update takes a second action.

### Confirmation

`web/src/layout/UpdateStatus.test.tsx` tests the deliberate touch and pointer actions and missing metadata.
`web/e2e/mobile-responsive.mjs` checks the update button is visible without installing on phone-sized touch viewports.
`tests/integration/web_update_test.py` tests the authenticated date and metadata boundaries.
