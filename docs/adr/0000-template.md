---
status: proposed
date: YYYY-MM-DD
implementation: planned
decision-makers: who decided (roles, optional)
---

<!--
MADR 4 (https://adr.github.io/madr/), trimmed. Keep it to a page or two; link details instead of copying them.
Front matter, flat `key: value` only:
  status            proposed | accepted | rejected | deprecated | superseded
  date              when the status last changed, YYYY-MM-DD
  implementation    built | partial | planned: how much of the decision the code does today, set from the code and the
                    pull requests in `implemented-by`; status says whether we agreed, this says whether it runs.
                    A superseded ADR keeps what was built before it was replaced.
  decision-makers   optional; roles, never names of people or organisations
  supersedes        optional; NNNN of the ADR this one replaces (that ADR gets `superseded-by: NNNN`)
  superseded-by     set on the old ADR when a new one replaces it; also set its status to superseded
  amends            optional; NNNN of an ADR this one changes in part (that ADR gets `amended-by: NNNN`)
  amended-by        set on the amended ADR; its status stays as it was
  amends-NNNN       one per amended ADR: what this ADR changes in it (a few words; shown in the index and on both pages)
  supersedes-NNNN   likewise for a superseded ADR, optional
  implemented-by    pull request numbers, comma separated, of the PRs that built it (not the one that only wrote the record);
                    leave it out until one exists
  related           optional; NNNN of ADRs this one builds on or refines without changing them (one-way)
Several numbers are separated by commas. Once accepted, only the front matter may change; a new ADR changes the rest.
The one exception is a dated amendment note at the end of More Information ("Amended YYYY-MM-DD: what changed, and
why"), for a correction that decides nothing new.
Delete this comment.
-->

# NNNN: Title that states the decision

## Context and Problem Statement

The situation and the question we must answer, in two to five sentences. Link sources for facts.

## Decision Drivers

- Optional: the forces and qualities that decide between the options.

## Considered Options

- **The chosen option** (chosen)
- **Another option**: why we did not take it.

## Decision Outcome

Chosen option: **the chosen option**, because it best meets the drivers. State it so it can be checked against the
code; the details follow.

- What exactly we do, as checkable statements.

### Consequences

- Good: what becomes easier.
- Bad: what becomes harder, and what we must now do.

### Confirmation

The enforcing mechanism, in two sentences, no file lists: what fails when the decision is broken, and where (a test,
lint rule, import-linter contract or CI job). If it is checked only in review, say so.

## More Information

Optional: related ADRs, research, open questions, when to revisit.
