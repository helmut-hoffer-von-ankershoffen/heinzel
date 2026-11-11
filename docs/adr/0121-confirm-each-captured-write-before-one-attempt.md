---
status: proposed
date: 2026-11-04
implementation: partial
related: 0049, 0068, 0099, 0115
---

# 0121: Confirm each captured write before one attempt

## Context and Problem Statement

A read lease or owner-designated artifact does not authorize a write. Generic document writes cannot bind an account
and provider-enforced revision. A provider response may be lost after a successful mutation, so retries can duplicate
an external effect. This foundation needs an exact, auditable host path without a standing grant.

## Considered Options

- **Captured conditional lease, exact human decision and durable attempt** (chosen).
- Reuse a generic write after reading: account continuity and atomic source-version enforcement are missing.

## Decision Outcome

Chosen option: **one captured conditional mutation per human approval**, because a draft must never grant authority.

- Immutable bounded terms name owner, session, provider binding, account, source, resource, operation, content, scope
  and base revision. Default providers deny. Existing sign-ins and scopes remain unchanged.
- Captured writes are a separate registered `CapturedWriteService`. Its concrete source-captured class declares
  `supported_operations: ClassVar[frozenset[str]]`, empty by default. The host currently accepts only the exact
  `insert-draft` declaration. Absent, inherited, mutable, unsupported or instance-shadowed declarations are refused.
  Legacy `ProviderLet.modes` continues to describe implemented protocol methods: a documents provider may implement
  only `read` while its separate captured service declares conditional drafting. No legacy write method is invented.
- The binding digest includes capability, concrete captured-service identity and declared operations alongside the
  installed distribution, source graph and approved runtime. Declaration and service drift fail closed, including
  around target observation, lease opening, review and mutation. A declaration never grants human approval.
- The selected-provider wrapper rechecks configuration, registration, source capture and service continuity around
  every lease action. A separately reviewed adapter must implement actual provider compare-and-set and synchronous
  quiescence; the host cannot sandbox trusted same-user provider code.
- The existing trusted chat boundary presents exact terms and issues a five-minute one-use proof. Authentication,
  model text, transport replies and local drafts are never approvals. The new flow is off by default behind
  `captured_writes`; no automatic scheduler or model write integration is activated.
- One durable control event consumes the approval and records the attempt before opening a mutation lease. The main
  writer remains held through conditional write, acknowledgement and cleanup; provider I/O holds no control lock.
- Missing acknowledgement, changed account/source, cleanup failure or lost receipt leaves an uncertain disposition.
  Reopening or repeating confirmation never retries an attempted effect. Only a recorded provider acknowledgement
  may be shown as acknowledged; a subsequent read is not proof that this host performed the write.
- Channel delivery/confirmation, other connected-helper behavior and the rolling-document workflow remain separate
  work. Bounded owner-private history/search and inert model drafting are source-prepared behind default-off flags. In particular this adds no standing append grant or
  automatic publish of edited content.

### Consequences

- Good: plugins can implement exact conditional writes with owner-private review and honest replay-safe outcomes.
- Bad: providers without atomic revision conditions must refuse; uncertain outcomes require external inspection.

### Confirmation

Focused provider fixtures prove foreign-owner/stale-proof denial, exact scope/source/account checks, durable intent
before mutation, cleanup fencing and no replay after unknown effects. Trusted chat integration verifies the same
proof and confirmation boundary; real provider and OS lifecycle acceptance remain distinct from these tests.
