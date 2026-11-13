---
status: proposed
date: 2026-11-13
implementation: planned
decision-makers: maintainer (product), architect (technical shape)
amends: 0049
amends-0049: a retired Heinzel can be deleted for good, leaving a tombstone without content
related: 0045, 0120, 0122
---

# 0148: Delete a retired Heinzel for good, after a typed confirmation

## Context and Problem Statement

[ADR 0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md) §7 lets a person retire a
personal Heinzel: it is hidden, never woken, and its whole history stays, so `restore` undoes it. ADR 0049 left open
whether a retired Heinzel's history may ever be deleted, and [ADR 0122](0122-preserve-authoritative-chat-history.md)
keeps chat history unless the owner expressly chooses deletion. People now want to remove a Heinzel they will never
need again, with everything it saw and wrote.

A Heinzel's private state today lives under `heinzel-sessions/<owner>/`: its main journal with turn bodies, its
control journal (profile, contracts, schedules, drafts, lifecycle), and its channel-processing lock folder, each one
directory named by the session id. Owner-wide stores reference it by id: the creation catalog, the model budget
ledger, the subscription slot ledger and the draft-target fences. The runner keeps the Heinzel's continuous chat
(`system.runner` chat folder, bound by `personal_session_id`). Search keeps a disposable in-memory index per session.
The session store indexes every folder in SQLite and reports a folder that disappears as "Session spine is missing",
and the owner check refuses a store folder the catalog does not explain, so removing a folder by hand reads as
tampering.

## Decision Drivers

- Irreversible on purpose: two deliberate steps, and nothing a stray click, a retry or an agent can do alone.
- Never half state: a crash at any point leaves either the Heinzel or a resumable deletion, never a damaged store.
- Owner-wide accounting stays true: budget caps and publication fences must not change because a Heinzel left.
- Every head offers the same operation with the same guard (ADR 0045: agents propose, the person confirms).

## Considered Options

- **Retire first, then delete for good with a typed confirmation; a content-free tombstone stays** (chosen).
- **Delete straight from the list**: one click away from losing history, and a running Heinzel could be mid-turn.
- **Keep the receipt-grade events in a shared state journal** (ADR 0049's recommendation): leaves descriptions of
  what the Heinzel did, which is the content the person asked to remove.
- **Rewrite the owner-wide ledgers without the Heinzel's lines**: the ledgers are append-only hash chains; rewriting
  them breaks their verification and would lower the owner's spent budget.

## Decision Outcome

Chosen option: **retire first, then delete for good with a typed confirmation**, because it keeps one undoable step
before the irreversible one, and removes everything that holds content while keeping the owner-wide accounting.

1. **Only a retired Heinzel can be deleted.** `hz heinzel delete SESSION_ID --name NAME --yes` refuses unless the
   Heinzel's latest lifecycle event is `heinzel.retired`, `--yes` is given, and `NAME` equals its current name
   exactly. The same operation is the MCP tool `heinzel_delete` and the REST call `POST /api/heinzel/delete`, with
   the same three checks in the operation itself, so no head can skip them. MCP marks it destructive, so an agent
   only proposes it and the person confirms.
2. **What is removed.** The main journal with every turn body (messages, replies, tool activity, drafts, lessons and
   anything the Heinzel remembered, which lives in its outputs); the control journal (profile, contract versions,
   schedules and their proposals, draft workflows and their approvals, retire and restore events); the
   channel-processing folder; each store's index rows and the session's lock files; the runner's continuous chat for
   this Heinzel with its proposals and pending MCP approvals; and the in-memory search index for it.
3. **What is kept.** In the owner catalog: the original `creation.reserved` record (request id, session id, a digest
   of the creation payload) and a new `creation.deleted` tombstone (session id and time, nothing else). In the
   owner-wide budget and subscription ledgers and draft-target fences: the existing records naming the session id
   with amounts, token envelopes and digests; they hold no message, name or document text. Nothing outside the
   machine is recalled: a message already sent over a channel, a notice already shown, or a draft already placed in
   a document stays where it went. Core keeps no per-Heinzel plugin state today; a plugin that stores some must
   offer its own delete until a later ADR adds a hook.
4. **Order, crash safety and resume.** Under the Heinzel's main writer (taken without waiting) and control writer,
   delete first appends `creation.deleting` to the owner catalog; from then on every read and write of that session
   is refused, and it leaves every list. It then removes, in this order: the runner chat, the channel-processing,
   control and main folders. The session store removes a folder by renaming it to a hidden name its index ignores,
   deleting its index rows, then deleting the renamed folder; each step is a no-op when already done. Last it
   appends `creation.deleted`. A crash after `creation.deleting` leaves a pending deletion that the next list,
   delete or verification completes; a crash before it leaves the retired Heinzel untouched. Running delete again
   on a deleted Heinzel returns the same tombstone.
5. **Running daemon and in-flight turns.** A retired Heinzel starts no turn, but one started before retiring may
   still run: delete then refuses with "it is finishing a turn; try again in a moment" instead of waiting. After
   `creation.deleting`, a writer that was waiting re-checks ownership once it holds the lock and refuses before
   writing; a folder it recreated is removed when the deletion completes. Daemon jobs read the catalog each tick
   and never see the session again; no restart is needed. A search index held by another process cannot be read
   (the ownership check refuses first) and is dropped when that process evicts it or restarts.
6. **Creation replay.** Replaying the creation request of a deleted Heinzel is refused; it never brings the session
   back. The owner store check expects exactly the folders of reserved sessions that are not deleted, and tolerates
   the folders of a deletion still in progress.
7. **The web flow.** The Retired list shows **Delete for good** next to Restore, in the design system's destructive
   style. It opens a dialog that says what is removed and that it cannot be undone, and enables its destructive
   button only after the person types the Heinzel's name. Phone and desktop layouts use the shared dialog.
8. **No undo.** There is no restore after delete, no trash and no grace period; the CLI help, the dialog and the
   user guide say so.

### Consequences

- Good: a person can remove a Heinzel and its history completely, from any head, with one rule for all of them.
- Good: the session store gains a safe, resumable remove, so deletion never looks like tampering.
- Bad: the catalog and the owner-wide ledgers keep growing by a few content-free records per deleted Heinzel.
- Bad: copies outside hz (channel messages, published drafts, external documents) stay; the guide must say so.

### Confirmation

Unit and integration tests remove each store and check it is gone, crash the deletion after each step and check a
retry finishes it, and refuse a Heinzel that is not retired, a wrong name and a missing `--yes`. An end-to-end
journey deletes a retired Heinzel through the dialog in Chromium and WebKit, phone layout included.

## More Information

This resolves ADR 0049's open question on deleting a retired Heinzel's spine: the command is `delete`, not `forget`,
and no receipt-grade event stays beyond the tombstone. It is the owner deletion choice ADR 0122 reserves.
