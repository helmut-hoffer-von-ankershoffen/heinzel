---
name: tracker-update
kind: responsibility
version: 1.0.0
description: >-
  Keeps each member's row of the Acme Payments tracker current every Wednesday, from
  that member's week, for the weekly report to read.
hz:
  schema: 1
  title: Tracker update
  schedule: "0 12 * * WED"
  outputs:
    row: {format: markdown}
---

# Tracker update

### Goal

The member's row in the team tracker says what they moved this week and what is next.

### Maintains

- `row`: three lines: done, next, blocked.

### Done when

- [ ] The row has all three lines.
