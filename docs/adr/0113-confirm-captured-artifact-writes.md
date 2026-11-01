---
status: proposed
date: 2026-10-30
implementation: partial
related: 0049, 0099
---

# 0113: Confirm captured artifact writes before a single attempt

## Context and Problem Statement

An observed read or owner-designated artifact does not authorize a write. The existing document write protocol has no captured account or provider-enforced revision precondition, and the current Google document adapter is read-only. How can a host record an exact human decision and an honest outcome without replaying an uncertain external effect?

## Considered Options

- **Use a separately reviewed captured write lease and durable attempt** (chosen).
- **Invoke the existing document write method after a read**: the method cannot bind the account or enforce the reviewed base revision.

## Decision Outcome

Chosen option: **use a separately reviewed captured write lease and durable attempt**, because a read or model proposal must not grant write authority.

- A bounded immutable draft binds owner, session, provider, account, source, exact resource, operation, content and provider-enforced base revision. The draft itself grants nothing.
- An adapter must implement a captured lease which re-observes those terms and promises a conditional write of that exact mutation. The default service denies; no Google write adapter or OAuth scope is added by this decision.
- A trusted one-use human confirmation is consumed in the same durable event as the attempt before any provider effect. The session main writer remains held until the call and cleanup are quiescent; the control writer is not held during provider I/O. An adapter that can leave detached work running cannot use this seam.
- A provider acknowledgement can yield a host-recorded receipt bound to the attempt, with an optional revision only when reported. A missing or uncertain response, cleanup or receipt does not establish success and is never retried automatically. Read metadata is not an edit receipt.
- Provider compare-and-set applies to the actual mutation. Local locks do not guarantee an atomic view of external state or confine same-user code. Existing provider login and scopes are unchanged.

### Consequences

- An owner can review an exact draft without implicitly authorizing access or future writes.
- Providers lacking a captured account, existing write scope or conditional mutation must refuse. A lost acknowledgement may require external inspection.

### Confirmation

Focused fake-provider state-machine and host tests must prove exact review, one-use approval, durable attempt before mutation, conditional revision, account drift denial, cleanup fencing, receipt provenance and no replay after lost acknowledgement. Until those tests and review exist, this decision is review only.
