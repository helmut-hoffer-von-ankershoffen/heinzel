---
status: proposed
date: 2026-11-13
implementation: planned
decision-makers: maintainer (product), architect (technical shape)
amends: 0049
amends-0049: a retired Heinzel can be deleted for good, leaving a tombstone without content
related: 0043, 0045, 0120, 0122
---

# 0148: Delete a retired Heinzel for good, after a typed confirmation

## Context and Problem Statement

[ADR 0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md) §7 lets a person retire a
personal Heinzel: it is hidden, never woken, and its whole history stays, so `restore` undoes it. ADR 0049 left open
whether a retired Heinzel's history may ever be deleted, and [ADR 0122](0122-preserve-authoritative-chat-history.md)
keeps chat history unless the owner expressly chooses deletion. People now want to remove a Heinzel they will never
need again, with everything it saw and wrote.

A personal Heinzel's private state lives in several places. Under `heinzel-sessions/<owner>/` are its main journal
with turn bodies, its control journal (profile, contracts, schedules, drafts, channel requests, lifecycle) and its
channel-processing lock folder, each a directory named by the session id, plus each store's SQLite index, which
copies every raw journal line. Owner-wide stores reference it by id: the creation catalog, the model budget ledger,
the subscription slot ledger and the draft-target fences. The runner keeps its continuous chat (bound by
`personal_session_id`). The notification store keeps its notices (route `/heinzel?chat=<id>`, ADR 0043). A channel
plugin can keep its own copy of an approval request it posted; heinzel-slack keeps the preview, detail and audit rows
in `approvals.sqlite3`. Search keeps a disposable in-memory index. The session store reports a vanished folder as
"Session spine is missing", and the owner check refuses a folder the catalog does not explain, so removing a folder
by hand reads as tampering.

## Decision Drivers

- Irreversible on purpose: two deliberate steps, and nothing an agent can start, even as a proposal.
- Never half state: a crash at any point leaves either the Heinzel or a resumable deletion, never a damaged store.
- Gone means gone on this machine: no byte of its content stays in a file, a free SQLite page or a plugin store.
- Owner-wide accounting stays true: budget caps and publication fences must not change because a Heinzel left.

## Considered Options

- **Retire first, then delete for good with a typed confirmation, never over MCP; a content-free tombstone stays**
  (chosen).
- **Delete straight from the list**: one click away from losing history, and a running Heinzel could be mid-turn.
- **Offer delete over MCP behind the terminal confirmation** used for schedule approvals: an agent could still start
  the flow, and nothing a Heinzel does needs to delete another one.
- **Keep the receipt-grade events in a shared state journal** (ADR 0049's recommendation): leaves descriptions of
  what the Heinzel did, which is the content the person asked to remove.
- **Rewrite the owner-wide ledgers without the Heinzel's lines**: they are append-only hash chains; rewriting breaks
  their verification and would lower the owner's spent budget.

## Decision Outcome

Chosen option: **retire first, then delete for good with a typed confirmation, never over MCP**, because it keeps
one undoable step before the irreversible one and removes everything that holds content, while the owner-wide
accounting stays. It covers personal Heinzels only; a team Heinzel's state on `hz/state` (ADR 0049 §10) is out of
scope.

1. **Who can delete.** `hz heinzel delete SESSION_ID NAME --yes` and the private REST call
   `POST /api/heinzel/delete` (the web app) delete. There is no MCP tool: the operation is declared for the CLI, web
   and private API heads only, and a test fails if the MCP head lists it. The operation itself refuses unless the
   Heinzel's latest lifecycle event is `heinzel.retired`, `yes` is true and `NAME` equals its current name after
   Unicode NFC normalisation, so no head can skip a check. The typed `NAME` makes the target explicit in scripts
   too; `--yes` states that the person read "cannot be undone".
2. **What is removed.** The main journal with every turn body (messages, replies, tool activity, drafts, lessons and
   what it remembered, which lives in its outputs); the control journal (profile, contract versions, schedules and
   their proposals, draft workflows and approvals, channel requests, retire and restore); the channel-processing
   folder; each store's index rows, written with SQLite `secure_delete` and followed by `VACUUM`, and every
   quarantined `.index-*.sqlite` copy in those stores; the runner chats bound to it or naming it as their context,
   with their proposals and pending MCP approvals; every notice whose route names it; the plugin's copy of each
   approval request it posted (statement 4); and the in-memory search index.
3. **What is kept.** In the owner catalog: the original `creation.reserved` record (request id, session id, a digest
   of the creation payload), the `creation.deleting` intent (session id, and channel names with the references of
   posted approval requests) and the `creation.deleted` tombstone (session id; the record's time). In the owner-wide
   budget and subscription ledgers and draft-target fences: existing records naming the session id with amounts,
   token envelopes and digests, and no text. Not recalled, because they left hz: messages already posted on a
   channel, notices already shown by macOS Notification Center, drafts already placed in a document, and the
   archive an explicit `notify-recovery` repair made (it stays bound to its audit receipt). The user guide, the
   privacy page and the CLI help say so.
4. **Channel plugins forget.** `ApprovalChannel.forget(reference)` is an optional hook with a no-op default: it
   deletes every copy the plugin keeps of that posted request and is idempotent. Delete records each posted approval's
   channel and reference in the intent, so the map survives the control journal. Finishing calls
   `settle(reference, "superseded")`, so no Approve button stays live, then `forget(reference)`. When a channel that
   holds posts is unavailable, delete refuses before the intent. heinzel-slack is the first implementer: its
   `forget` deletes the request's rows, preview, detail and audit lines, with `secure_delete`. That ships in
   heinzel-plugins in the same release as this Core change.
5. **Locks and checks.** Delete takes the main writer and the channel-processing writer without waiting (a running
   turn or a channel reply in flight refuses: "it is finishing; try again in a moment"), then the control writer,
   then the catalog writer for the intent; this is the order turns use. Under these locks it checks retired, the
   name, and that no `draft.target-intent` of this Heinzel lacks an outcome (an unsettled document append refuses;
   it would fence that document for every Heinzel). Runner chats are removed under the runner's proposal lock and
   each chat's own lock, without waiting.
6. **Order, crash safety and resume.** After `creation.deleting`, every read and write of the session is refused and
   it leaves every list. Finishing then forgets the channel posts, removes the runner chats and the notices, and
   removes the channel-processing, control and main folders. The session store removes a folder by renaming it to a
   hidden `.removed-<id>` name its index ignores, deleting its index rows, vacuuming, then deleting the renamed
   folder; each step is a no-op when already done. Last comes `creation.deleted`. A crash after the intent leaves a
   pending deletion that the next delete or list of Heinzels finishes; a crash before it leaves the retired Heinzel
   untouched. Delete again on a deleted Heinzel returns the same tombstone without the name check.
7. **Late writers and the running daemon.** Every session writer, and every shared ledger writer, re-checks the
   catalog after taking its lock; a writer that waited past the intent refuses before writing and removes a folder
   it recreated. A store writer also refuses a lock won on a folder that was renamed away. Daemon loops read the
   live catalog each tick and skip a Heinzel deleted mid-tick instead of aborting; no restart is needed. A search
   index held by another process cannot be read (the ownership check refuses first) and is dropped when that
   process evicts it or restarts.
8. **One catalog projection.** Every reader takes live, deleting and deleted from one projection of the catalog
   (`reserved_session_ids`, `deletion_state`): ownership, the list of Heinzels, input receipts, cross-Heinzel search,
   and the store check, which expects the folders of live sessions and tolerates those of deleted or deleting ones
   under their own or hidden names. Replaying a deleted Heinzel's creation request is refused.
9. **Downgrade.** A release before this one reads the new catalog records as unknown and reports the owner stores as
   needing repair; it fails closed and never deletes or rewrites anything. Downgrading after a deletion is not
   supported: the release notes say so, and upgrading again restores normal operation.
10. **The web flow.** The Retired list shows **Delete for good** next to Restore in the design system's destructive
    style. It opens a type-to-confirm dialog, a new shared component, which says what is removed and that it
    cannot be undone, and enables its destructive button only after the typed name matches (NFC). After success the
    tab clears that Heinzel's session-storage drafts. Phone and desktop use the same dialog.
11. **No undo, no content in logs.** There is no restore after delete, no trash and no grace period. Log lines
    about a deletion carry the session id at most.

### Consequences

- Good: a person can remove a Heinzel and its history completely from the CLI or the app, with one rule for both.
- Good: the session store gains a safe, resumable remove, so deletion never looks like tampering.
- Bad: the catalog and the owner-wide ledgers keep growing by a few content-free records per deleted Heinzel.
- Bad: copies outside hz stay (channel posts, Notification Center, published drafts); the guide must say so.
- Bad: a channel plugin that keeps copies must implement `forget`; until it does, its copies stay.

### Confirmation

Unit and integration tests remove each store and check it is gone, crash the deletion after each step and check the
next list finishes it, refuse a Heinzel that is not retired, a wrong name, a missing `--yes` and an unsettled
document append, and assert the MCP head does not list the operation. A canary test plants one unique string in a
message, the name, a notice, a runner chat and a channel request, deletes, and scans every byte under the test's
home and state directories, SQLite files included; an end-to-end journey deletes through the dialog in Chromium and
WebKit, phone layout included.

## More Information

This answers ADR 0049's open question on deleting a retired Heinzel's spine: the command is `delete`, not `forget`,
and no receipt-grade event stays beyond the tombstone. It is the owner deletion choice ADR 0122 reserves.
