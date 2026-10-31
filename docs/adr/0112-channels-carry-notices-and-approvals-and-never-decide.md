---
status: proposed
date: 2026-10-31
decision-makers: maintainer
related: 0028, 0043, 0094
---

# 0112: Channels carry notices and approvals and never decide

## Context and Problem Statement

A helper's owner should be reached where they are: a native banner, a direct message. They should be able to answer
there too. Core names no vendor, and a capability binding selects exactly one adapter, while a person wants several
channels at once.

## Considered Options

- **Two abstract service classes that plugins subclass and register, found by base class** (chosen).
- A capability binding per channel: one adapter per capability, so a second channel would need a second capability.
- A new kind of let: more surface than a service needs, with nothing to declare beyond the class.

## Decision Outcome

`NotificationChannel` delivers one notice and returns a receipt that says what was observed. `ApprovalChannel` shows
one request bound to a digest and expiry, and reports the replies it saw. `Kernel.implementations(base)` finds them.

A channel carries words and never decides. An `ChannelApprovalReply` is a claim: the host checks the actor against
the owner's identity from the channel's own sign-in, and the digest and expiry against the exact pending request,
before consuming an approval through the same typed path as any other head. Which helper may use which channel is
reviewed in that helper's schedule.

### Consequences

- Good: native notifications and chat channels plug in without Core knowing either; a delivery receipt can say
  `unknown` rather than claim delivery.
- Bad: services found by base class are not listed in the capability matrix until a later change adds them.

### Confirmation

`tests/unit/kernel/channels_test.py` covers finding channels and the bounds of their messages. A channel plugin's own
tests cover delivery; a real receipt on the real provider proves it.
