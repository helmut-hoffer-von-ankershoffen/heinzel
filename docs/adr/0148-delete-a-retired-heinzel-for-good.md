---
status: proposed
date: 2026-11-13
implementation: partial
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

- Irreversible on purpose: two deliberate steps, and a person in the loop for the second one: no MCP tool, a typed
  name in the app, and on the command line only someone typing the name at an interactive terminal.
- Never half state: a crash at any point leaves either the Heinzel or a resumable deletion, never a damaged store.
- Gone means gone on this machine: no byte of its content stays in a file, a free SQLite page or a plugin store.
- Owner-wide accounting stays true: budget caps and publication fences must not change because a Heinzel left.

## Considered Options

- **Retire first, then delete for good with a typed confirmation, never over MCP; a content-free tombstone stays**
  (chosen).
- **Delete straight from the list**: one click away from losing history, and a running Heinzel could be mid-turn.
- **Offer delete over MCP behind the terminal confirmation** used for schedule approvals: an agent could still start
  the flow, and nothing a Heinzel does needs to delete another one.
- **`--yes` plus the name as arguments on the CLI**: any coding agent with a shell can pass both; the CLI cannot tell
  a person from an agent by its arguments.
- **Keep the receipt-grade events in a shared state journal** (ADR 0049's recommendation): leaves descriptions of
  what the Heinzel did, which is the content the person asked to remove.
- **Rewrite the owner-wide ledgers without the Heinzel's lines**: they are append-only hash chains; rewriting breaks
  their verification and would lower the owner's spent budget.

## Decision Outcome

Chosen option: **retire first, then delete for good with a typed confirmation, never over MCP**, because it keeps
one undoable step before the irreversible one and removes everything that holds content, while the owner-wide
accounting stays. It covers personal Heinzels only; a team Heinzel's state on `hz/state` (ADR 0049 §10) is out of
scope.

1. **Who can delete.** The boundary, stated plainly:
   - The CLI is interactive only. `hz heinzel delete SESSION_ID` asks for the name at an interactive terminal; it
     refuses when standard input is not a terminal, refuses a name passed as an argument (the option is hidden from
     its help) and has no `--yes`. A script or a pipe cannot answer it. Resuming a started deletion asks nothing.
   - MCP does not expose delete: the operation is declared for the CLI, web and private API heads only, and a test
     fails if the stdio, HTTP or check-in MCP head lists it.
   - The app's Retired list opens a type-to-confirm dialog; it calls the private REST operation
     `POST /api/heinzel/delete` with the name the person typed, which needs the private UI token.
   - Not a boundary: an agent running as the same operating-system user with a shell can obtain that token (for
     example through `hz ui open`) and call the private API. Separating a person from such an agent needs
     operating-system isolation, which is out of scope here. The privacy page and the user guide say so.

   The operation refuses unless the Heinzel's latest lifecycle event is `heinzel.retired` and the typed name equals
   its current name after Unicode NFC normalisation, so no head can skip a check.
2. **What is removed.** The main journal with every turn body (messages, replies, tool activity, drafts, lessons and
   what it remembered, which lives in its outputs); the control journal (profile, contract versions, schedules and
   their proposals, draft workflows and approvals, channel requests, retire and restore); the channel-processing
   folder; each store's index rows, written with SQLite `secure_delete` and followed by `VACUUM`, and every
   quarantined `.index-*.sqlite` copy in those stores; the runner chats bound to it or naming it as their context,
   with their proposals and pending MCP approvals; every notice whose route names it, in the notification store and
   in every `notify-recovery` archive (each archive's receipt is rebound to the new digest and records how many
   notices were removed, never which); the plugin's copy of each approval request it posted (statement 4); and the
   in-memory search index.
3. **What is kept.** In the owner catalog: the original `creation.reserved` record (request id, session id, a digest
   of the creation payload); the `creation.deleting` intent (session id, and the channel name, request id and, when
   known, reference of each approval request); one `creation.settled` and one `creation.forgotten` record per
   request (session id, channel name, request id); and the `creation.deleted` tombstone (session id; the record's
   time). In the owner-wide budget and subscription ledgers and draft-target fences: existing records naming the
   session id with amounts, token envelopes and digests, and no text. Not recalled, because they left hz: messages
   already posted on a channel, notices already shown by macOS Notification Center, and drafts already placed in a
   document. The user guide, the privacy page and the CLI help say so.
4. **Channel plugins forget.** `ApprovalChannel.forget(request_id, reference)` is an optional hook with a no-op
   default: it deletes every copy the plugin keeps of one approval request, locally and offline, is idempotent and
   treats an unknown request as forgotten. `request_id` is what hz passed to `post`; `reference` is the receipt's
   reference, or None when no receipt was recorded, because a plugin may store its copy before the post returns.
   Delete records, in the intent, the channel and request id of every approval it proposed on a channel, with the
   reference when known, so the map survives the control journal; a proposal whose body is no longer retained still
   counts when its receipt names the channel. Before the intent, delete refuses only when a plugin that holds
   requests is not loaded or is turned off, naming the plugin and the fix; being offline or signed out never refuses.
   While finishing, hz calls `settle(reference, "superseded")` once per posted request so no Approve button stays
   live: a network call, best effort, bounded by the plugin's own timeouts, recorded as a content-free
   `creation.settled` record whatever it answered, never repeated. Then it calls `forget(request_id, reference)`.
   heinzel-slack is the first implementer and ships with or before this change: its `forget` deletes the request's
   rows, preview, detail and audit lines with `secure_delete` on, then runs `VACUUM` and
   `PRAGMA wal_checkpoint(TRUNCATE)` (its store uses WAL, and older row versions sit in free pages and WAL frames),
   and the plugin carries its own canary test with a positive control. That is the acceptance condition for the
   heinzel-plugins head.
5. **Locks and checks.** Delete takes the main writer and the channel-processing writer without waiting (a running
   turn or a channel reply in flight refuses: "it is finishing; try again in a moment"), then the control writer,
   then the catalog writer for the intent; this is the order turns use. Under these locks it checks retired, the
   name, and that no `draft.target-intent` of this Heinzel lacks an outcome (an unsettled document append refuses;
   it would fence that document for every Heinzel). Runner chats are removed under the runner's proposal lock and
   each chat's own lock, without waiting.
6. **Order, crash safety and resume.** After `creation.deleting`, every read and write of the session is refused and
   it leaves every list. Finishing removes hz's own content first: the runner chats, the notices and archived
   notices, then the channel-processing, control and main folders, and the search index. Each removal survives a
   crash at any point:
   - The session store renames a folder to a hidden `.removed-<id>` name its index ignores, deletes its index rows,
     runs `VACUUM`, then deletes the renamed folder; each step is a no-op when done, and `VACUUM` runs on every
     attempt, not once.
   - The runner moves each chat folder, under its lock, out of the chat root into `chat-removed/`, where no reader
     looks, then deletes it; every attempt sweeps `chat-removed/` first. A chat whose file names the Heinzel but
     cannot be read keeps the deletion pending instead of being skipped.
   - A recovery archive's receipt first records the pending redaction with the new file's digest, then the archive
     is rewritten, then the receipt is bound to it; a retry that finds the pending digest on disk finishes the
     receipt.

   If `system.runner` or `system.notify` is turned off, delete refuses up front, and a deletion already started
   stays `forgetting`, naming the module, until it is on again. Then each plugin forgets its requests; every success
   is recorded as a content-free `creation.forgotten` record, and the request stays in the intent until it is
   forgotten. A module or plugin that cannot forget yet never holds hz's own removal back: delete answers
   `forgetting` with what is still waiting, the app says so, `hz system doctor` reports each unfinished deletion
   by id with what it waits for and the fix until it is done, and a list retries with backoff from a minute to an
   hour. Last comes `creation.deleted`; it is never written while anything waits. A crash after the intent leaves a
   pending deletion that the next delete or list finishes; a crash before it leaves the retired Heinzel untouched.
   Deleting again returns the same result without asking for the name.
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
   needing repair. The blast radius is the whole owner: on the older release every one of their Heinzels shows
   "needs repair", not only the deleted one. It fails closed and never deletes or rewrites anything, and upgrading
   again restores normal operation. The only automatic rollback, the update's startup check, runs before the new
   release does, so it cannot meet these records; only a manual downgrade can, and the release notes say so. The
   tolerant readers are a separate commit from the writer, so a release train that can ship them one release
   earlier removes this case.
10. **The web flow.** The Retired list shows **Delete for good** next to Restore in the design system's destructive
    style. It opens a type-to-confirm dialog, a new shared component, which says what is removed and that it
    cannot be undone, and enables its destructive button only after the typed name matches (NFC). After success the
    tab clears that Heinzel's session-storage drafts. Phone and desktop use the same dialog.
11. **No undo, no content in logs.** There is no restore after delete, no trash and no grace period. Log lines
    about a deletion carry the session id at most.

### Consequences

- Good: a person can remove a Heinzel and its history completely from a terminal or the app, with one rule for both.
- Good: the session store gains a safe, resumable remove, so deletion never looks like tampering.
- Bad: the catalog and the owner-wide ledgers keep growing by a few content-free records per deleted Heinzel.
- Bad: copies outside hz stay (channel posts, Notification Center, published drafts); the guide must say so.
- Bad: a channel plugin that keeps copies must implement `forget`; until it does, its copies stay.

### Confirmation

Unit and integration tests remove each store and check it is gone, crash the deletion after each step and check the
next list finishes it, and refuse a Heinzel that is not retired, a wrong name, a non-interactive or argument-named
CLI call and an unsettled document append; a test asserts the MCP head does not list the operation. A canary test
plants unique strings in a message, the name, a notice, a recovery archive, a runner chat and a channel request,
first proves the scan finds them in every kind of store (a positive control), deletes, and scans every byte under the
test's directories again, dotfiles and SQLite journal, WAL and shared-memory files included, for an ASCII canary
and a non-ASCII one in UTF-8 and JSON-escaped form. An end-to-end journey deletes through the dialog in Chromium and
WebKit, phone layout included.

## More Information

This answers ADR 0049's open question on deleting a retired Heinzel's spine: the command is `delete`, not `forget`,
and no receipt-grade event stays beyond the tombstone. It is the owner deletion choice ADR 0122 reserves.
