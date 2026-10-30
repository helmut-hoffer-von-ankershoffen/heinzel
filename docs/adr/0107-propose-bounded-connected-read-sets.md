---
status: proposed
date: 2026-10-30
related: 0049, 0099, 0104
---

# 0107: Propose bounded connected read sets separately

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
- An internal host-observed review rechecks every selected provider and account; it is not an atomic multi-provider
  snapshot. A separately scoped trusted human confirmation may issue and consume a one-use, expiring proof of the
  exact current review, recording the whole set approval atomically. Any changed entry requires a new review and
  approval; revocation applies to the whole set. Neither source text, a model nor a legacy single grant can approve it.
- This slice has no public approval issuer or operation, provider read execution, model tool or schedule integration.
  Any later execution must freshly revalidate every relevant source, account and approval at the read boundary.
- Existing fixed and rolling grant approvals, histories, schedules and digests retain their meanings. No legacy grant
  or accepted artifact becomes an approval for a read set.

### Consequences

- Good: a bounded proposal can be inspected without granting external access.
- Bad: an approved set alone cannot execute a multi-source journey; receipts, scheduling and effect controls remain.

### Confirmation

`tests/unit/modules/connected_read_set_test.py` checks proposal bounds, duplicate terms, owner/session binding,
idempotency, projection and legacy isolation. `tests/unit/modules/connected_read_set_review_test.py` checks fresh
observations and exact intents. `tests/unit/modules/connected_read_set_approval_test.py` checks trusted proof,
consumption, replay, revocation and legacy isolation. Review confirms that no provider read execution is added.
