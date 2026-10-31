---
status: proposed
date: 2026-10-27
decision-makers: maintainer
amends: 0048
related: 0039, 0045, 0088
implemented-by: 240
---

# 0089: One selected workspace panel on every screen size

## Context and Problem Statement

Chat, a terminal and World Clock can all be open. Stacking them divides an already short phone viewport into
unusable pieces. Future contributed panels need a common presentation rather than another layout exception.
The maintainer requested a mobile workspace switcher and desktop tabs in the right sidebar.

## Considered Options

- **One selected panel with a shared tab strip** (chosen): gives the active task the available space.
- **Stack open panels**: increasingly small panes make controls and content inaccessible on phones.

## Decision Outcome

Chosen option: **one selected panel with a shared tab strip**, because the same interaction scales from phones
to the desktop sidebar and accepts additional panel descriptors without bespoke sizing.

- `WorkspacePanels` takes stable panel ids, labels and contents. Desktop places it beside the page; narrow screens
  place it below, with a Page tab that returns to the screen without closing sessions. The header and footer stay
  visible while the workspace scrolls, except in full-screen terminal. On phones the
  selected task uses the content area; keyboard and short-screen modes compact the controls while keeping panel switching available. Tabs select one open panel and support arrow keys, Home and End.
- Selecting another tab hides the prior panel without unmounting it. Drafts, terminal sessions and clock state
  survive switching and resizing. Closing a panel remains explicit and releases its resources as before.
- Panel descriptors form the frontend presentation contract. This does not add a Python plugin API or a new let;
  any future plugin panel API must feed the same renderer and receive its own architecture review.
- Terminal full screen remains available; switching to another panel leaves full screen.

### Consequences

Each panel gets usable space, and the renderer can accommodate more descriptors with horizontal tab scrolling.
Users see one panel at a time instead of simultaneously stacked chat and terminal views.

### Confirmation

`WorkspacePanels.test.tsx` verifies selection, keyboard navigation and retained mounted state. `Panel.test.tsx`
checks shell integration and responsive placement. The mobile browser sweep opens chat, terminal and World Clock
together, switches among them and checks bar positions while scrolling at phone sizes.
