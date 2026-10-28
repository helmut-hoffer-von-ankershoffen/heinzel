---
status: proposed
date: 2026-10-28
decision-makers: maintainer
amends: 0047
related: 0003, 0019, 0039, 0046
---

# 0091: Confirmed plugin adds use server-owned operation receipts

## Context and Problem Statement

A browser can lose the response to a confirmed plugin installation and reload while the server is still working.
Client storage cannot coordinate other tabs or paired devices, and replaying the request may run the installer twice.
The server needs to own the confirmation identity and preserve enough result state to reconcile a later request.

## Considered Options

- **Opaque preview receipt, durable status and one claim per receipt** (chosen): exact preview and confirmation share a
  server-owned identifier, with a durable result for later reads.
- **Browser storage or an idempotency header created by the client**: tabs and devices can choose different keys, and
  stored client state cannot establish what the server completed.
- **Deduplicate every matching plugin requirement**: a later preview may be an intentional reinstall, so separate
  preview receipts remain separate intents.

## Decision Outcome

Chosen option: **opaque preview receipt, durable status and one claim per receipt**, because the server can bind the
user's confirmation to the exact candidate and own its reconciliation state.

- A preview creates an unguessable receipt bound to the candidate as shown. Confirming by receipt installs that exact
  candidate once; reusing the receipt returns or reports its current state. Existing confirmation without a receipt
  remains supported for CLI, MCP and REST callers.
- Receipt creation and state transitions use private files and cross-process locks below the configured state folder.
  A confirmed operation is marked running before its installer starts. Success stores the result; an interrupted or
  uncertain run becomes unknown and is never replayed automatically.
- Receipt lookup is a normal authenticated plugin operation, available through the same heads as plugin changes.
  The web client persists only the opaque receipt ID and queries the server after reload.
- Separately previewed receipts are separate user intents and do not coalesce.

### Consequences

- Good: tabs and paired devices that share one preview receipt can reconcile one server-owned operation after a lost
  response or reload.
- Bad: legacy direct confirmations without a receipt retain their existing behavior and do not gain lost-response
  deduplication. Interrupted receipt-backed work needs explicit user resolution; independent previews remain separate
  confirmations.

### Confirmation

Unit tests cover receipt persistence, process locking, exact-candidate binding, duplicate confirmation and interrupted
operation reconciliation. Web tests cover reload status reconciliation and retry prevention.
