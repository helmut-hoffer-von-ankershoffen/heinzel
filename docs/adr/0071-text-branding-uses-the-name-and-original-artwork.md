---
status: proposed
date: 2026-10-25
decision-makers: maintainer
amends: 0016
related: 0046
---

# 0071: Text branding uses the name and original artwork

## Context and Problem Statement

[ADR 0016](0016-name-heinzelmaennchen.md) used a generic elf emoji where only text fits. The maintainer wants the
original Heinzel artwork and plain text instead, with consistent wording across CLI and UI.

## Considered Options

- **Original artwork with plain text branding** (chosen).
- **Keep the generic elf emoji**: the maintainer requested its removal.

## Decision Outcome

Chosen option: **original artwork with plain text branding**, because the app's own character identifies it.

- Use the name Heinzelmännchen without a generic elf emoji in CLI help and API/MCP titles.
- The tagline is "helpers accelerating product teams", beside the original artwork. CLI help does not repeat the
  brand description below Usage. About information explains the elves from Cologne.
- The footer shows `vX.Y.Z`, with a bear icon and "built with AI in Berlin" aligned right when space permits.
  Narrow layouts retain the information through wrapping or the version pane.
- Keep the original terminal artwork and UI avatar. Historical ADRs and embedded-workflow markers retain their
  recorded meaning; this changes branding rather than artifact discovery.

### Consequences

- Good: text and graphical surfaces share one identity and description.
- Bad: text-only clients show the name without a character icon.

### Confirmation

`tests/unit/heads/cli_branding_test.py` checks the footer's width and wording and refuses the generic elf emoji in
head source. The UI footer tests check the same description and attribution.
