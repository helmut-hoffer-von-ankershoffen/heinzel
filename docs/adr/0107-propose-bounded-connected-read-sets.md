---
status: proposed
date: 2026-10-30
implementation: built
related: 0049, 0099, 0104
implemented-by: 299
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
- A direct user chat may suggest up to four fixed reads in a separate inert, versioned draft. The person can inspect
  and select those terms for preparation; suggesting them contacts no provider and confers no approval. Preparation
  observes the selected providers, and the subsequent exact review still needs separate human confirmation.
- An internal host-observed review rechecks every selected provider and account; it is not an atomic multi-provider
  snapshot. A separately scoped trusted human confirmation may issue and consume a one-use, expiring proof of the
  exact current review, recording the whole set approval atomically. Any changed entry requires a new review and
  approval; revocation applies to the whole set. Neither source text, a model nor a legacy single grant can approve it.
- An internal executor may read one exact approved entry for an authenticated human-owned active turn, after
  rechecking its source, account and approval. It records a private per-entry receipt before returning a value; an
  interrupted attempt is not replayed automatically. The private interactive route exposes only approved opaque handles
  after an accepted turn starts; the host binds the requested handle to the attempt and receipt. A separate trusted
  human confirmation stages and approves the exact reviewed set; a model cannot stage that confirmation. This route
  does not grant daemon or scheduled execution, item-evidence effects or notification authority.
- Existing fixed and rolling grant approvals, histories, schedules and digests retain their meanings. No legacy grant
  or accepted artifact becomes an approval for a read set.

### Consequences

- Good: a bounded proposal can be inspected without granting external access.
- Bad: scheduling, item evidence and effect controls require separate authority and design.

### Confirmation

`tests/unit/modules/connected_read_set_test.py` checks proposal bounds, duplicate terms, owner/session binding,
idempotency, projection and legacy isolation. `tests/unit/modules/connected_read_set_review_test.py` checks fresh
observations and exact intents. `tests/unit/modules/connected_read_set_approval_test.py` checks trusted proof,
consumption, replay, revocation and legacy isolation. `tests/unit/modules/connected_read_set_input_test.py` and
`tests/unit/modules/connected_read_set_key_test.py` check turn/handle binding, receipts, fail-closed key recovery and
legacy compatibility; `tests/integration/source_read_set_prototype_test.py` checks captured-source failure.
`tests/integration/read_set_interactive_prototype_test.py` exercises private human confirmation and a receipted fake
interactive read as well as a draft-only chat suggestion. The mounted chat component tests cover explicit review and
uncertain confirmation recovery. Review confirms there is no daemon, schedule or notification route for this executor.
