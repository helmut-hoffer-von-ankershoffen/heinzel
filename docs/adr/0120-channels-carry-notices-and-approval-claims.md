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
- Each posting needs its own owner/helper-bound proposal and separate CLI or paired-web human confirmation. The
  exact source capture, signed-in actor, posting account, destination, scope and complete message are shown together.
  Consenting to post an approval request does not approve the captured write. No global setting grants posting rights.
- Channels expose `delivery_target` and conditional `post_reviewed` / `deliver_reviewed`. The supplied destination
  must be bound at the actual transport mutation; source checks before/after a call alone cannot prevent a target race.
  Legacy implementations default to refusal and must adopt this contract before host posting can work.
- Before posting, the host consumes a one-use exact human proof into its private durable outbox. The attempt precedes
  the transport effect. Missing acknowledgements, exceptions and source/destination drift are unknown and never
  automatically reposted. Refused/queued/delivered remain distinct observations; even refused items are not replayed.
- A posted approval carries the complete bounded serialized captured-write review (including target, account, scope,
  section and base revision), its argument digest and expiry. Oversized reviews are refused rather than truncated.
- An approval reply remains a claim. The host polls only consented recorded references, then verifies captured channel
  source, linked actor, destination, exact request/digest, posting time, reply time and current expiry. It re-observes the
  pending captured-write source/account/scope/revision before claiming that decision in the owner's private journal.
- The daemon keeps its real authentication context. A private host-scoped exact-draft confirmation lets this consumer
  use the existing captured-write human issuer and single-use execution path; there is no `CurrentUser.local("cli")`
  elevation, model self-approval or exposed operation accepting a verified outcome.
- An owner/session channel-processing lock serializes reply claim, execution and recovery. A crash after a claim never
  repeats the write. Recovery closes it as uncertain; durable settlement retries only closing the same posted request.
  Provider acknowledgement remains separate evidence and no uncertain result is upgraded by guessing.
- `connected_channels` defaults off, independently of `captured_writes`. The core registers the generic consumer and
  a daemon polling job, but no concrete channel, credentials, scopes, notification replacement or plugin dependency.
  Existing chat history retains proposal-only semantics. This is partial source preparation; schedule/onboarding UI,
  live provider acceptance and the remaining connected-helper journeys are separate obligations.

### Consequences

- Good: independently released providers can implement the same transport contracts.
- Bad: contracts and offline fixtures do not establish real delivery or a complete channel approval journey.

### Confirmation

Focused offline tests check separate posting consent, full exact preview, authenticated-head behavior, linked actor,
request/digest/time checks, installed source continuity, stale captured-write terms, uncertainty and replay. The full
journey still requires independent security review, released compatible SDK/plugins and real human/provider acceptance.
