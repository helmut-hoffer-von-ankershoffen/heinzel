---
name: weekly-report
kind: responsibility
version: 1.2.0
description: >-
  Keeps the Acme Payments weekly report drafted every Friday from the tracker's
  rows, for the engineering manager to approve and send.
hz:
  schema: 1
  title: Weekly report
  schedule: "0 17 * * FRI"
  inputs:
    tracker: {read: "contract:team/tracker-update#row", stage: draft}
  outputs:
    report: {format: markdown}
  publishes:
    - action: teamwork.send-report
      output: report
---

# Weekly report

### Goal

The week's report says what the team shipped, what moved and what is stuck.

### Requires

- `tracker`: every member's row of the tracker.

### Maintains

- `report`: at most 300 words in three parts.

### Done when

- [ ] Every part draws on the tracker's rows.
