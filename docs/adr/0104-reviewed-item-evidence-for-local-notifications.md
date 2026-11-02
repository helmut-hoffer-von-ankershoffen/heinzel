---
status: proposed
date: 2026-10-30
implementation: built
related: 0049, 0067, 0099
implemented-by: 291
---

# 0104: Bind notification deduplication to observed source items

## Context and Problem Statement

An approved connected read currently records only a keyed comparison of the complete result. A scheduled notification
can use a model-chosen topic, which does not establish that two topics describe the same source item. A person may
want a separate bounded policy that queues at most once for an item, or once for each observed item revision.

## Considered Options

- **Host-minted item references from a reviewed provider declaration and an approved read** (chosen).
- **Trust model-chosen identifiers**: a model can invent or vary them across turns.
- **Treat complete-result hashes as item identities**: result order, pagination and other items change independently.

## Decision Outcome

Chosen option: **host-minted item references from a reviewed provider declaration and an approved read**, because
only the host can bind an observed item to the owner, agent, approved source, operation and exact read. A provider's
stable identity and revision claims must be explicitly described and reviewed; metadata is not a grant and a read
does not imply complete source history. The host extracts bounded item fields from the already returned result,
records private keyed fingerprints without raw source identifiers, and exposes only opaque references for the current
turn. Invalid, duplicate, missing or unsupported identities fail closed; a changed source, grant, owner, key or
contract cannot silently reuse evidence.

A separately approved scheduled notification policy may require current observed item evidence and deduplicate by
item or item revision. Rate bounds and durable attempted-before-effect records still apply. An uncertain attempt is
not replayed automatically. The earlier free-topic policy, fixed notices, schedules and approvals retain their exact
meaning and gain no new authority. Neither queue acceptance nor a receipt proves an operating system displayed a
notice. No provider reads, writes or notifications happen solely because a declaration exists.

### Consequences

Providers need an auditable item-identity contract and users must separately approve any new recurring notification
policy. Missing item evidence fails closed rather than inventing a notification trigger. Stored keyed references
still require careful deletion and key-loss handling; provider identifiers are not proof of real-world identity.

### Confirmation

Focused tests must cover owner and grant isolation, duplicate and forged references, incomplete reads, changed source,
revision handling, restart, uncertain outcomes and unchanged legacy bindings. Integration and independent security
review are required before the policy or any provider declaration is enabled.
