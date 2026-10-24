---
status: accepted
date: 2026-10-23
---

# 0001: Record architecture decisions

## Context and Problem Statement

Heinzelmännchen is built by people and agents together. Agents have no memory of past discussions; people forget
them. Without a written record, decisions get relitigated or silently reversed.

## Considered Options

- **Architecture Decision Records in the repository** (chosen)
- **Wiki pages**: drift from the code and are invisible to agents working in the repository.

## Decision Outcome

Chosen option: **Architecture Decision Records in `docs/adr/`**, because agents and people find them next to the
code they govern.

- Every decision that shapes the code — structure, dependencies, interfaces, processes — is recorded as an ADR in
  `docs/adr/`, in [MADR](https://adr.github.io/madr/)-style format (copy `0000-template.md`), numbered sequentially.
- Accepted ADRs are never rewritten; a new ADR supersedes them.

### Consequences

- Good: agents read ADRs before changing structure (AGENTS.md rule 1); the `architect` agent checks changes against
  them.
- Neutral: writing an ADR is part of the change that needs it (skill: `write-adr`).

### Confirmation

`test_adrs_follow_the_template` and `test_adr_index_and_links_match_the_records` in
`tests/unit/documentation_test.py` check the template, the index and supersession links; the `architect` agent and
review check that structural changes come with an ADR.
