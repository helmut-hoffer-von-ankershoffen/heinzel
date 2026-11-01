---
status: proposed
date: 2026-10-30
implementation: built
decision-makers: maintainer
related: 0049, 0115
---

# 0116: Preserve authoritative chat history independently of model context

## Context and Problem Statement

People need to return to older user and agent messages even when a model turn can accept only a bounded amount of
context. ADR 0049 describes expiring conversation bodies with the draft-retention setting. That policy would remove
the text needed for later navigation, although bounding a model prompt does not require deleting authoritative chat.

## Considered Options

- **Keep durable chat by default and bound each model context separately** (chosen).
- Expire authoritative chat on the existing draft-retention schedule: this silently loses older conversation text.
- Send complete history to every model turn: private data exposure and context cost grow with the conversation.

## Decision Outcome

This decision amends ADR 0049 only for authoritative user and agent chat messages: a future conversation-body
retention implementation must default to keeping them indefinitely, while honoring a separately expressed owner
retention choice. It must not silently change an existing owner's explicit choice, delete existing history, or treat
model-context omission as consent to deletion. A model turn may receive a bounded recent selection and indicate
when earlier material was omitted; authoritative journals and message bodies remain separate from that selection.

Existing retention settings for workflow drafts, fetched inputs and other non-chat data retain their own meanings.
This decision does not widen access, override an owner's deletion choice, or promise that damaged or externally
removed bodies can be recovered. It does not itself add a new setting or a session-pruning job. The currently reviewed
implementation's `heinzel:prune` sweeps workflow drafts and records; no session-body deletion was found in this scope.

### Consequences

Keeping chat can consume increasing local storage. Any future owner-controlled retention or deletion implementation
must state its scope and preserve explicit choices, while allowing expired or missing bodies to be represented as gaps.
Bounded model context and bounded result pages do not imply bounded journal metadata scans or total indexing work.

### Confirmation

Focused fake-owner tests create more messages than the context window, verify only the selected tail is hydrated for
the recent-context helper, and confirm earlier messages and journal events remain readable. Separate search tests
verify that externally removed bodies are not returned from the disposable index. No test demonstrates a production
session-pruning feature, a live provider, or recovery of deleted content.
