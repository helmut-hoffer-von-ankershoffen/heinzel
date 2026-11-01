---
status: accepted
date: 2026-10-23
implementation: built
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
- Accepted ADRs are not rewritten: only the front matter and a dated amendment note at the end of More Information
  ("Amended YYYY-MM-DD: what changed, and why") may change. A new ADR changes or supersedes the decision.
- `implementation: built | partial | planned` in the front matter says how much of the decision the code does today.
  `status` says whether we agreed; `implementation` says whether it runs.
- Confirmation is the enforcing mechanism, in two sentences, no file lists.

### Consequences

- Good: agents read ADRs before changing structure (AGENTS.md rule 1); the `architect` agent checks changes against
  them.
- Neutral: writing an ADR is part of the change that needs it (skill: `write-adr`).

### Confirmation

`test_adrs_follow_the_template` and `test_adr_index_and_links_match_the_records` in
`tests/unit/documentation_test.py` check the template, the index and supersession links; the `architect` agent and
review check that structural changes come with an ADR.

## More Information

Amended 2026-11-01: added the `implementation` key, defined Confirmation as the enforcing mechanism, and allowed
dated amendment notes. Older ADRs keep their long Confirmation lists.
