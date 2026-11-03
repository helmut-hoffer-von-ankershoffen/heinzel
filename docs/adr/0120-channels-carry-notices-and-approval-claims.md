---
status: proposed
date: 2026-11-04
implementation: partial
related: 0028, 0043, 0068, 0094
---

# 0120: Channels carry notices and approval claims

## Context and Problem Statement

Plugins need a provider-independent way to carry bounded notices and exact approval previews to an owner's chosen
channel. A capability selects one adapter, while a person may choose several channels. Transport identity and delivery
are observations; neither a posted message nor a reply is authority to execute a write.

## Considered Options

- **Ordinary abstract service contracts discovered by base class** (chosen).
- A new let kind or one capability per channel: unnecessary registration concepts for ordinary services.

## Decision Outcome

Chosen option: **ordinary service contracts**, because plugins can register implementations without core naming vendors.

- Notification and approval messages are bounded typed values. Receipts distinguish delivered, queued, refused and
  unknown; an unknown outcome cannot justify an automatic retry.
- Kernel service discovery returns registered subclasses in stable order, retaining normal singleton resolution.
- An approval reply is only a claim. Before any future channel consumer issues a one-use approval, it must bind the
  authenticated owner and linked actor, channel and posted reference, exact full preview digest, pending request,
  issue/expiry times and current source revision, then record settlement durably. Text grants no authority.
- This foundation exports the ports and discovery only. It registers no channel, native-notification replacement,
  credential expansion, background delivery or reply-to-confirmation job. Per-helper channel consent, durable delivery
  and verified reply consumption remain outstanding; a plugin can use the contracts without gaining those effects.

### Consequences

- Good: independently released providers can implement the same transport contracts.
- Bad: contracts and offline fixtures do not establish real delivery or a complete channel approval journey.

### Confirmation

Focused channel tests check registry discovery, singleton behavior and message bounds. No consumer may treat a typed
reply as authority; complete owner-bound transport confirmation requires its own security review and real acceptance.
