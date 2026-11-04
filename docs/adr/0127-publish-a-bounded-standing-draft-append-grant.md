---
status: proposed
date: 2026-11-04
implementation: planned
related: 0049, 0099, 0115, 0120, 0121
---

# 0127: Publish a bounded standing grant for draft append

## Context and Problem Statement

A recurring helper must put each new release's poem into the owner's supplied rolling document before requesting
human review. The owner explicitly requested unattended draft append; requiring another insertion approval on every
release would contradict that workflow. The exact-content confirmation required by ADR 0121 remains necessary for
arbitrary writes and must not become a general standing permission.

## Considered Options

- **A separate owner-published draft-append grant** (chosen).
- Require exact insertion approval for every draft: prevents the requested unattended drafting stage.
- Treat a prompt or model proposal as general write consent: lets untrusted text broaden authority.

## Decision Outcome

Chosen option: **a separate bounded draft-append grant**, because initial trusted owner publication authorizes this
specific recurring effect while every later share still needs an exact human decision.

- An explicit trusted owner CLI publication binds the owner and helper, observed release source, provider source
  digest, account, exact supplied rolling-document reference and section, append-marked-draft operation, cadence,
  content bounds and selected review-notice target. Model text, fetched documents and transport replies cannot
  create or expand this grant. No document ACL is changed.
- Publication records an explicit initial feed baseline. Host-derived release keys and immutable observed release
  content digests survive restart as deduplication tombstones. A changed tag or duplicate identity cannot create a
  second append. Catch-up follows recorded cursors rather than guessing from model titles or a wall-clock window.
- A distinct default-deny SDK service supports conditional append of a marked draft and capture of its edited
  revision. Existing captured-write services and exact-content approvals retain their current behavior.
- The host chooses the release identity from the bound observed source, chooses the fixed destination and adds
  the draft marker. The model supplies bounded poem text only. The same release cannot cause a second automatic
  append, including after restart or a lost acknowledgement.
- The host journals an attempt before its conditional append. A lease must enforce the observed base revision at
  the mutation and become synchronously quiescent on close. Source, account or scope drift, missing acknowledgement
  or cleanup failure leaves an uncertain outcome; observing later content never upgrades it into an acknowledgement.
- A shared target fence serializes cooperating draft workers and grant generations. An uncertain attempt blocks
  conflicting automatic target writes until safe explicit reconciliation. Revocation enters under the same fence
  and waits for in-flight work to quiesce; it cannot undo a request the provider already accepted.
- Existing configured turn, daily budget and rate limits apply. Recurrence continues until revoked; a finite expiry
  or total quota is added only when the owner explicitly selects it. Permission and limits are rechecked before
  every effect, including notices and share attempts.
- Human editing is data, not approval. After editing, the host captures the document reference, account, source,
  current revision and bounded edited draft text for a separate exact human share review. That review also binds
  the publication channel's captured source, account, destination, scope and complete link message.
- Human confirmation alone permits one journaled share attempt. It shares a link to the rolling document, which
  remains mutable by design; it does not claim frozen document contents or an atomic cross-provider transaction.
  Unknown transport outcomes never trigger an automatic repost.
- Append, review-notice, human decision, share, confirmation-notice and uncertain dispositions remain visible in
  the helper's authoritative chat. Revocation, uncertain attempts and exact provider receipts survive restart.
- The complete workflow stays off by default. Core uses declared generic ports and gains no dependency on concrete
  plugins or providers. SDK and adapter publication, real provider acceptance and activation remain separate gates.

### Consequences

- Good: the requested unattended drafting stage works without weakening arbitrary-write approval or share consent.
- Bad: adapters need genuine conditional append and source/account continuity; older adapters must refuse this path.

### Confirmation

Review only until the runtime and its tests exist. Required acceptance includes the real installed CLI and daemon
with isolated provider doubles, restart and lost-acknowledgement cases, edited-revision review, revocation and exact
destination controls; real human/provider acceptance remains separate from those source fixtures.
