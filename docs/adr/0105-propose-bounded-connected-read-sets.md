---
status: proposed
date: 2026-10-30
related: 0049, 0099, 0104
---

# 0105: Propose bounded connected read sets separately

## Context and Problem Statement

[ADR 0099](0099-reviewed-connected-reads-and-local-agent-notifications.md) binds a connected read to one reviewed
source and account. A durable agent may need more than one source, such as a bounded list and bounded messages, but
existing single-read consent cannot authorize a combined read. How can a person prepare a bounded set without
changing existing grants or creating authority from a proposal?

## Considered Options

- **Record a distinct inert, bounded read-set proposal** (chosen).
- **Combine existing approvals implicitly**: a person has not reviewed their use together or their exact combined terms.

## Decision Outcome

Chosen option: **record a distinct inert, bounded read-set proposal**, because preparation must not create access.

- A private owner/session-scoped proposal holds one to four uniquely identified entries. Each carries exact read
  terms and a host-issued selector; repeated selectors or repeated source/account/operation/arguments are refused.
- Proposals are immutable, versioned separately, replay only with identical terms, and are projected separately from
  single-grant history. Their recorded origin is provenance, never an authority or approval.
- This initial slice cannot approve a set, invoke a provider, register a model tool or change a schedule. Any later
  execution requires an explicit human review of the exact set and independent source/account checks for every read.
- Existing fixed and rolling grant approvals, histories, schedules and digests retain their meanings. No legacy grant
  or accepted artifact becomes an approval for a read set.

### Consequences

- Good: a bounded proposal can be inspected without granting external access.
- Bad: the proposal alone cannot execute a multi-source journey; separate review, receipts and effect controls remain.

### Confirmation

`tests/unit/modules/connected_read_set_test.py` checks proposal bounds, duplicate terms, owner/session binding,
idempotency, projection and legacy isolation. Review confirms that no provider execution or approval path is added.
