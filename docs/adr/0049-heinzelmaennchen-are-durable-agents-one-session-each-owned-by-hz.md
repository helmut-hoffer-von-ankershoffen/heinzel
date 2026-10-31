---
status: proposed
date: 2026-10-24
decision-makers: maintainer (product), architect (technical shape)
amends: 0010, 0020, 0021, 0028, 0029, 0038, 0045, 0052
amends-0010: a turn of a continuous session instead of a session per render
amends-0020: the tick wakes Heinzelmännchen by key; team state on hz/state with leases
amends-0021: one contract per Heinzelmännchen, in versions; records become turn bodies
amends-0029: the runner no longer owns the session
amends-0038: lessons; hz drafts and hz approve
amends-0045: the sidebar chat with a Heinzelmännchen is its session
amends-0028: in the durable-turns amendment, fingerprints per facet
amends-0052: in the durable-turns amendment, downstream wakes only when the approved output moved
amended-by: 0052, 0081
related: 0012, 0022, 0024, 0027, 0030, 0035, 0037, 0040, 0043, 0046, 0050, 0051
implemented-by: 165, 167, 263
---

# 0049: Heinzelmännchen are durable agents: one session each, owned by hz

## Context and Problem Statement

Today a contract is rendered by "one bounded agent session per run" ([ADR 0010](0010-towards-a-reactive-workflow-engine.md),
[ADR 0021](0021-workflow-contracts.md) §6): every run starts from nothing, gets the inputs as data and leaves a draft, a
`.record.json` and a receipt. Nothing carries over but the files. The chat ([ADR 0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md))
keeps its own sessions, apart from the contracts it talks about. The maintainer decided the product direction
(2026-10-24, design doc): a **Heinzelmännchen** is a durable agent with a stable id and **one continuous session for its
whole life**; every wake-up (its schedule, a source that changed, a source newly discovered, the user asking) is another
turn in that session. It acts with its user's identity and logins, drafts in place with a DRAFT tag, and does nothing
beyond without approval. It has exactly one **contract** (what it keeps true, from which sources, by when, who approves),
written in plain language in the artifact's Heinzel tab or in chat, compiled once into a precise form and approved;
contracts evolve by **amendments**, proposed by the Heinzelmännchen or the user, approved and versioned, and every draft
records the contract version that produced it. After each approval it diffs draft against approved text and decides on
its own which edits are **lessons**, visible to the user as "what I learned from you"; it never changes its own
permissions. A user has hundreds of them, most asleep and cheap to wake. Calling Claude Code headless leaves the
transcript with Claude Code, not with the Heinzelmännchen. What is a Heinzelmännchen's session, where does it live, how
does it survive crashes and sleep, what of it leaves the machine, and how do hundreds stay cheap?

## Decision Drivers

- Tenet 4 (humans own, with lineage): a draft must say which contract version made it and who approved it; a lesson
  must be visible and reversible; the session is the lineage of one asset, owned by its user, not by a model vendor.
- Tenet 8 (open and independent): the transcript's format belongs to hz, so any model or runner continues it
  ([ADR 0029](0029-agent-runners-are-capability-adapters.md); the chat already resends its transcript for that reason,
  ADR 0045).
- Done, not nagged (tenet 3) and "hundreds per user": a Heinzelmännchen asleep must cost no process, no open file and
  no model call; waking one must cost one query and one bounded turn.
- Laptops sleep and processes die mid-turn ([ADR 0020](0020-daemon-head.md)): a turn resumes where it stopped and never
  places a draft twice or spends twice (the "durable runs" idea of the research note of 2026-10-24).
- Nothing is sent home ([ADR 0027](0027-nothing-is-sent-home.md)); what goes to the team's repository is lineage without
  content ([ADR 0021](0021-workflow-contracts.md) §13, the never-commit list of the "core and skills" note).
- Python decides *whether, when, where, as whom and at what cost*; the model decides *what* (the "core and skills"
  note, §2). Pruning, summarising and the choice of what a turn sees are deterministic, hz's job, not the model's.

## Considered Options

- **One append-only JSONL session per Heinzelmännchen (a small spine kept for life, turn bodies that expire), a
  derived SQLite index, checkpoints per turn step with idempotency keys; hz owns the session and builds every turn's
  context** (chosen)
- **Resume the runner's own session** (Claude Code `--resume <session-id>`, its JSONL under `~/.claude/projects`): no
  format of ours, no versioning, no pruning we control, per machine and per vendor; the transcript leaves hz when the
  runner changes. ADR 0045 rejected it for the chat for the same reason.
- **Stateless runs plus "the last approved text and the diff in the packet"** ([ADR 0038](0038-drafts-are-approved-in-place.md)
  §4, the status quo): it carries corrections forward but has no identity, no lessons the user can see and undo, no
  amendments, no conversation; the chat about a contract and the contract's runs stay two things.
- **One SQLite database as the session store** (Pi Durable's harness: conversation entries, documents and tasks in one
  committed store): atomic and queryable, but a binary file the user can't read, diff or hand to another tool, and a
  schema migration on every change. Kept for the *index*, which is derived and can be rebuilt; the record of what
  happened is text.
- **Pi Durable itself** (the pi project's own durable harness, `@earendil-works/pi-durable`, in the same monorepo as
  the pi coding agent, [earendil-works/pi](https://github.com/earendil-works/pi): checkpointed tasks in SQLite,
  idempotent submissions, forkable conversations): three weeks old, experimental ("the API changes without notice"),
  TypeScript, and it owns the agent loop. We borrow its rules (checkpoint every phase; a side effect reruns after a
  crash only if declared replay-safe; idempotency keys on every write; memoised approval decisions) and build them in
  Python on the ledger we have. *Attribution amended 2026-10-24:* this option first called it "Earendil's TypeScript
  library" as if it were apart from pi; pi (`pi-ai`, `pi-agent-core`, `pi-coding-agent`) and Pi Durable are one
  project, the coding agent being the non-durable loop and Pi Durable its durable restatement
  ([announcement](https://earendil.com/posts/pi-durable/),
  [spec](https://github.com/earendil-works/pi/tree/main/packages/durable)). The verdict stands and its spec
  strengthens it: one process owns a storage at a time, the JSONL backend keeps history in memory, the API has no
  stability promise. Statements 12–21 below borrow pi's two queues and Pi Durable's replay and parking rules.
- **OpenProse Reactor as the wake runtime** (added 2026-10-24; [openprose/reactor](https://github.com/openprose/reactor),
  the alpha reference harness that keeps a set of `*.prose.md` contracts true: a compile step lowers contracts to a
  subscription graph, canonicalizers and validators, and a deterministic reconciler decides skip, render, commit or
  propagate from fingerprints): rejected as a runtime, because it renders through a vendor agent SDK with a provider
  key (against [ADR 0035](0035-integrations-go-through-vendor-clis-as-an-ordinary-employee.md) and ADR 0030), lets a
  model compile what is material (hz keeps that judgment in Python, ADR 0021), ships a flat 60-second poll, has no
  actor or timestamp on its receipts and makes no compatibility promise. Its reconciler's rules are borrowed
  (statements 15 and 20): a schedule is a recheck, not a render; a skip costs nothing and is recorded; stage the input
  before the wake; fingerprints per facet; single flight per node with coalescing; wakes ordered by graph height;
  downstream wakes only when the approved output moved ([ADR 0052](0052-the-graph-emerges-from-recorded-reads-and-writes.md)
  §7); cost rolled up by wake cause.
- **A central service holding sessions** (a hosted database, a vendor's "agent memory"): against ADR 0027 and the
  no-server design of ADR 0020; identity would stop being the user's own logins.
- **Receipts as the session** (grow ADR 0020's hash-chained receipts until they hold the conversation): receipts are
  lineage without content and are kept forever; a conversation holds content and must expire. Two things, one chain
  each; the spine *references* receipts.

## Decision Outcome

Chosen option (proposed): **a Heinzelmännchen is a stable id, one contract with versions and one session hz owns: an
append-only JSONL spine kept for its life, turn bodies that expire, a derived SQLite index that lists hundreds at once;
every wake is a turn whose steps are checkpointed with idempotency keys; lessons and amendments are events in the same
session; what leaves the machine is lineage without content.** Checkable statements follow; the turn engine is
[ADR 0050](0050-pydantic-ai-runs-a-turn-a-coding-agent-is-a-handoff.md), the module and its commands
[ADR 0051](0051-the-workflow-module-becomes-the-heinzel-module.md).

1. **Identity.** A Heinzelmännchen has an **id** (`hz-` and a 26-character ULID, minted at creation, never reused, the
   directory name and the key everywhere), a **name** (a slug like today's contract names, unique per namespace, shown
   to people, renamable) and a **namespace** (`me` or `team`, [ADR 0021](0021-workflow-contracts.md) §10). It belongs
   to one **user**: the person who joined and whose logins it uses. "User" is the word for someone who has joined (the
   maintainer's decision); `Person` stays the directory's SCIM record of the org chart ([ADR 0024](0024-people-and-org.md)).
   It acts as its user through the vendor CLIs ([ADR 0035](0035-integrations-go-through-vendor-clis-as-an-ordinary-employee.md))
   and the user's own model login or key ([ADR 0030](0030-credentials-live-in-the-os-keychain.md)); there is no
   service account and no central server. A draft tag names the id and the contract version
   ([ADR 0038](0038-drafts-are-approved-in-place.md) §2).
2. **Exactly one contract, in versions.** The contract is ADR 0021's `.prose.md` text (frontmatter under `hz:`, an
   OpenProse-compatible body); nothing about its format changes here. Version 1 is compiled from plain language (a
   sentence in chat, the artifact's Heinzel tab, a playbook template, [ADR 0037](0037-plugins-bring-playbooks.md)) and
   shown; the user approves it, and only then does the Heinzelmännchen wake on its own. Later versions come from
   **amendments**: a proposed text, by the Heinzelmännchen (from what it learned, a source it discovered, a question it
   couldn't answer) or by the user (in chat or as a file edit), approved with `hz approve`, compiled, numbered `n+1`.
   Every version is kept (`contract/v<n>.prose.md`, its compiled form and its intent fingerprint, ADR 0021 §8). An
   amendment the Heinzelmännchen proposes may change the body and may *narrow* the frontmatter (fewer inputs, a lower
   cap, a later deadline); one that *widens* it (`requires`, `inputs`, `publishes`, `agent.max_usd`, `schedule`,
   `approvers`) is refused by the compiler (`HZC050`) and must be written by the user. That is how "it never changes its
   own permissions" is enforced in code, not in a prompt. Lessons (6) never touch the contract.
3. **One session, two kinds of files.** Under `<data dir>/heinzel/<id>/`:
   - `session.jsonl`, the **spine**: one JSON object per line, append-only, kept for the Heinzelmännchen's life. Each
     line is an envelope `{v, seq, ts, prev, turn, kind, key, data}`: `v` the envelope version (1), `seq` a counter,
     `ts` UTC, `prev` the SHA-256 of the previous line (the chain of ADR 0020 §8: it detects truncation and hand edits,
     not tampering), `turn` the turn id, `kind` the event kind, `key` the idempotency key when the event is a step (5),
     `data` the kind's payload. Kinds are additive; a kind whose payload changes incompatibly gets a new name
     (`draft.placed.2`), readers skip kinds they don't know, and `v` changes only if the envelope does. The spine holds
     no draft text, no fetched input and no model message: it holds ids, hashes, sizes, costs, dispositions and the
     short texts the user is meant to read (a lesson, an amendment, a question, a summary, a report line). Budget: a
     turn adds about a kilobyte; a Heinzelmännchen with a daily schedule stays under a megabyte for years.
     *Amended 2026-10-24:* new kinds, the frozen vocabulary and migration on read are statement 18.
   - `turns/<turn>.jsonl`, the **body** of one turn: the messages exchanged with the model, tool arguments and results,
     the inputs as fetched, the draft's text, a handoff's transcript (ADR 0050). It is what the `.record.json` is today
     and expires like it: `[heinzel] retention_days` (default 90, 0 keeps forever; a project `hz.toml` can't set it)
     after the turn, deleted by the job `heinzel:prune` and the operation `hz heinzel prune`, never by a read
     ([ADR 0021](0021-workflow-contracts.md) §13's rules: only files hz wrote, `O_NOFOLLOW`, idempotent). The spine
     references every body by turn id and content hash, so a body that is gone is a gap the user can see, never a break
     in the chain.
   - Files are private (0700/0600) and written atomically with fsync, like receipts.
4. **Turns and their causes.** Every wake is a turn with one cause, recorded in `turn.started`: `schedule` (the
   contract's slot, [ADR 0020](0020-daemon-head.md) §4), `source-changed` (an input's fingerprint differs from the one
   the last turn saw, ADR 0021 §8), `discovered` (a playbook hint or the user's artifacts turned up a source the contract
   doesn't name), `asked` (the user, in chat or with `hz heinzel wake`), `approval` (a placed draft was approved or
   rejected, ADR 0038 §3: the learning turn, 6) and `created` (the first turn, which compiles version 1). A turn is a
   fixed sequence the engine walks and the model fills: *gather* (inputs through the capability handles the contract
   declares, as today) → *think* (model and tool steps, ADR 0050) → *outcome* (a structured result: a draft, lessons, an
   amendment, a question for the user, or nothing to do, with a report line) → *act* (hz, in code: place the draft with
   its tag, record the lesson, file the amendment, ask the question) → *finish* (`turn.finished`: disposition
   `drafted | asked | learned | proposed | skipped | failed | abandoned`, cost, duration). The chat with a
   Heinzelmännchen is the same session: a message is a turn of cause `asked`, the sidebar chat with one selected *is*
   its session, and the general chat of ADR 0045 stays for everything that is not about one Heinzelmännchen. Which
   turns the next turn sees is hz's choice (8), never the model's. *Amended 2026-10-24:* a turn may also wait
   (statement 12), has the further causes `retry` and `stale` and may carry several causes at once (13), takes chat
   messages while it runs (14), and `skipped` has sub-reasons (15).
5. **Crash safety: a checkpoint per step, an idempotency key per effect.** Each step appends its event before the next
   starts. A wake has a key: `<id>@<slot>` for `schedule`, `<id>@<input>:<fingerprint>` for `source-changed`,
   `<id>@<request id>` for `asked` (the chat's message id); a tick that finds a `turn.started` with that key and no
   `turn.finished` **resumes** that turn at its last checkpoint, one with a `turn.finished` skips it (the daemon's
   idempotency rule of ADR 0020 §4, now per turn). Reads and model calls are replay-safe: rerunning them costs money
   within the cap and nothing else, so an interrupted *think* step is rerun from the last recorded model message. Effects
   are not: `place_draft` carries the key `<id>@v<contract version>@<slot>@<asset>` and the adapter looks for a tag with
   that key before writing (ADR 0038 §2's methods gain the key), a question is asked once per key, a notice
   ([ADR 0043](0043-desktop-notifications-one-policy-shown-once.md)) once per key. A turn that overran its timeout is
   `abandoned` and never resumed, as a job is today. The SQLite index (7) is derived: it is updated after the spine
   line is durable, carries the `seq` it has seen, and a mismatch on open rebuilds it from the spines
   (`hz heinzel reindex` does it on demand). Nothing a crash can leave behind needs repair by hand. *Amended
   2026-10-24:* effects are journalled in a ledger, tools declare whether they are replay-safe, and a turn resumed too
   often is `abandoned` (statement 16); a resumed turn runs under the versions it started with (17).
6. **Lessons.** When an `approval` turn finds the draft approved, the model gets the draft as placed and the text as
   approved (both from the body and the adapter) and decides, as a structured outcome, which edits are lessons: a
   sentence each ("Priya moves risks above the summary", "no adjectives in headings"), with the diff hunks they come
   from. Each is a `lesson.recorded` event; the active lessons are part of every later turn's instructions, after the
   contract and before the inputs. The user sees them as "what I learned from you" (`hz heinzel show`, the page) and can
   dismiss one (`lesson.dismissed`; it is kept, marked, and never shown to the model again). A lesson is advice about
   *how*, never a permission: it can't add an input, a target or a tool, which is why it lives outside the contract (2).
   The approver, the diff and the share of words changed are recorded as ADR 0038 §4 says; "the next render gets the
   last approved text and the diff in its packet" becomes "the next turn has the lessons and, through `last_approved`,
   the text": the same lineage, now decided and explained.
7. **The index: hundreds, listed at once.** `<data dir>/heinzel/index.sqlite` holds one row per Heinzelmännchen (id,
   name, namespace, state, contract version and fingerprint, assets, next due slot, watched inputs with their last
   fingerprints, last turn, pending draft or amendment or question, cost today) and one per turn, draft, lesson and
   amendment. Every row is derived from spine events, so the index is rebuildable and never authoritative. `hz heinzel
   list` filters by artifact (asset ref), state and contract (name, version, a frontmatter key) in every head and never
   opens a spine. States: `proposed` (version 1 not yet approved), `asleep`, `awake` (a turn is running), `waiting` (a
   draft awaits approval, or a question or amendment awaits the user), `paused` (by the user, or a team contract whose
   pinned content changed, ADR 0021 §10) and `retired` (kept, never woken; `hz heinzel retire`, reversible).
   *Amended 2026-10-24:* `waiting` also covers a turn that waits for a time or a change (statement 12); the row then
   carries what it waits on.
8. **What a turn sees: deterministic context, hz's summaries.** The model's instructions are, in this order: hz's
   framing (what a Heinzelmännchen is and may not do), the contract's body at the current version, the skills that apply
   ([ADR 0050](0050-pydantic-ai-runs-a-turn-a-coding-agent-is-a-handoff.md)), the active lessons. Its messages are: the
   last `summary.written`, every spine event since it rendered compactly (turn, cause, outcome, report line), the last
   approved text when there is one, and this wake's inputs as data in `<input>` elements, framed as never being
   instructions ([ADR 0021](0021-workflow-contracts.md) §4). Bodies are never needed to continue, so expiry never costs
   continuity. When the events since the last summary exceed a budget (`[heinzel] summary_after_turns`, default 20, or
   24,000 characters), the turn ends with the model writing a summary as a structured outcome ("what I know about this
   asset and this user"), recorded as `summary.written` and readable by the user. Pruning what the model sees is this
   rule and nothing else; no model decides what to forget. *Amended 2026-10-24:* the summary's schema, chaining and
   the turns kept verbatim beside it are statement 19; dismissed lessons and redactions apply as `context.edit` (18).
9. **Scale: asleep costs nothing, a tick costs one query.** An asleep Heinzelmännchen is rows in the index and files on
   disk: no process, no open file, no model call. The daemon's tick ([ADR 0020](0020-daemon-head.md)) asks the index
   for what is due (slots since the last tick) and for the watched inputs, polls each **source** once per tick whatever
   the number of Heinzelmännchen that watch it (one fetch per `(capability, method, args, window)`, conditional where
   the adapter can, hashed as today), and wakes those whose fingerprint moved. Wakes per tick are bounded
   (`[heinzel] max_wakes_per_tick`, default 10; the rest wait for the next tick, oldest due first), and so is spending:
   the contract's `agent.max_usd` per turn, `[heinzel] max_usd` per turn whatever the contract says, and
   `[heinzel] max_usd_per_day` across all of a user's Heinzelmännchen; a turn that would cross a cap is `skipped` with
   the reason and the user is told once a day. Discovery runs as a daily job over the playbooks' hints
   ([ADR 0037](0037-plugins-bring-playbooks.md)) and the team's inventory, and wakes a Heinzelmännchen with cause
   `discovered` at most once per source. Creation stays trivial: a sentence in chat, a template, a proposal by another
   Heinzelmännchen (`hz approve` creates it), never a form. *Amended 2026-10-24:* "each source once per tick" becomes
   "each input when its recheck is due", inputs are staged before anyone wakes, wakes are ordered by graph height, and
   cost is rolled up by cause (statement 20); one writer per spine (21).
10. **What leaves the machine.** The session never does. For a team Heinzelmännchen, its **receipt-grade events** go to
    the team repository's machine branch `hz/state` (the "core and skills" note's option B: reviewed truth on `main`,
    machine state on an orphan branch, leases as refs): `turn.finished` (cause, disposition, cost, the contract
    fingerprint, input fingerprints, the draft's hash), `draft.placed` (key, asset ref, hash), `approval.observed`
    (approver's roster handle, share changed, the approved text's material fingerprint `output_fp`, no text; `output_fp`
    amended 2026-10-24, [ADR 0052](0052-the-graph-emerges-from-recorded-reads-and-writes.md) §7) and `contract.amended`
    (version, fingerprint); they are the
    action log's entries ([ADR 0052](0052-the-graph-emerges-from-recorded-reads-and-writes.md)). Never a body,
    never a lesson or a summary (they are about one user's edits), never a name or an address
    ([ADR 0024](0024-people-and-org.md) §6). Those events *are* ADR 0020 §8's receipts for compiled jobs: a turn writes
    one receipt, the spine keeps its id, and the local ledger stays as it is. Who runs a team Heinzelmännchen is the
    note's rule: its owner's daemon, a named delegate's when the owner's hasn't ticked within two intervals, decided
    by a lease ref `refs/hz/lease/<id>` with an expiry and a fencing token that `turn.started` records. Leases, the
    orphan branch and the lint that refuses text in it are ADR 0020's remaining work, unchanged in shape; this record
    fixes what a session contributes to them.
11. **The user's view.** `hz heinzel list | show | chat | wake | new`, `hz drafts`, `hz approve` and the MCP tools of the
    same names ([ADR 0051](0051-the-workflow-module-becomes-the-heinzel-module.md)); the web app's "My Heinzelmännchen"
    list and a page per Heinzelmännchen: the contract and its versions, the timeline of turns, the lessons, the pending
    draft, question or amendment, and the sidebar chat as its session. `hz drafts` is a projection over the index (every
    `waiting` draft with its place), not a store: the inbox as a view ([ADR 0038](0038-drafts-are-approved-in-place.md)
    §8). *Amended 2026-10-24:* `hz heinzel retry`, `resume` and `stop` join them (statements 13 and 14); a page or
    chat that opens mid-turn gets the current row and then deltas, never a replay (21).

**Amended 2026-10-24: durable turns.** Proposed by the architect from research the same day into the pi agent loop
and Pi Durable ([earendil-works/pi](https://github.com/earendil-works/pi)), the durable-execution engines (Temporal,
Restate, DBOS, Inngest, Cloudflare Agents, LangGraph, the Pydantic AI integrations) and OpenProse Reactor
([openprose/reactor](https://github.com/openprose/reactor)); it is written to agree with the triggers and wake
loop a later ADR will record. The engines converge on a few rules: journal before the effect and keep the effect
outside the write; replay by stable name; at-least-once inside and a stable key at the edge; waiting is a stored
condition plus a wake, never a blocked process; a human decision is memoised. Statements 1–11 hold; these are added.

12. **A turn can wait.** Besides running (`awake`) and finished, a turn may be **waiting**: the step that decides to
    wait appends `turn.waiting {step, until?, on?}`, where `until` is a UTC time ("give the reviewer until noon") and
    `on` one condition: `{kind: source, input, fingerprint_not}` (continue when the input moved), `{kind: approval,
    key}` (a named draft was approved or rejected) or `{kind: answer, key}` (a question of 18 was answered). Both
    together are a wait with a timeout, Restate's awakeable raced against a timer
    ([Restate](https://docs.restate.dev/develop/ts/awakeables)). A waiting turn holds no process, no open file and no
    lock; it is one row in the index's turn table with its condition. The Heinzelmännchen's own state (7) is `awake`
    while any turn of it runs, else `waiting` while a turn waits or an item awaits the user. Every tick asks the index
    for waiting turns whose `until` passed or whose `on` matched and resumes each at its checkpoint with
    `turn.resumed {woken_by: timer | source | approval | answer | asked}`, so the turn knows which fired; `asked` is
    `hz heinzel resume <name>`, the user saying go on now (`hz heinzel wake` still starts a new turn, 4). A wait
    without `until` gets `[heinzel] max_wait` (default 7 days) as its `until`; the turn's timeout counts its running
    time, not its waiting. Other wakes don't queue behind a waiting turn: they run their own turns, and the waiting
    one resumes when its condition holds and no turn is `awake`. When one approval both resumes a turn waiting `on`
    it and starts the `approval` learning turn (6), the waiting turn resumes first. When another turn has meanwhile
    placed a draft for the same output, the waiting turn is finished `abandoned` with reason `superseded` instead of
    resuming under an older version (17). Nothing sleeps and no timer object outlives a reboot, because none exists:
    the condition is data, as in Pi Durable's `sleeping(until)` with the host's `onWake` and Cloudflare's alarms
    ([Pi Durable spec](https://github.com/earendil-works/pi/tree/main/packages/durable),
    [Cloudflare](https://developers.cloudflare.com/agents/api-reference/schedule-tasks/)). A contract's deadline is a
    different thing: the index's `next_due`, which marks a result late and never pauses a turn.
13. **Causes `retry` and `stale`, and several causes per turn.** `retry` is a turn forked from another turn's
    checkpoint: `turn.started` names `parent_turn`, `from_step` (by default after *gather*, so inputs are not fetched
    again) and the user's `hint`; both bodies are kept and the parent's draft is marked `superseded` in the index
    (`hz heinzel retry <name> "shorter"`, or "try again, shorter" in its chat); its key is
    `<id>@retry:<parent turn>:<request id>`. It is pi's `/fork` and LangGraph's fork from a checkpoint without a tree
    in the spine, which stays a chain
    ([pi sessions](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/session-format.md),
    [LangGraph](https://docs.langchain.com/oss/python/langgraph/use-time-travel)). It is not the contract's `retries`,
    which rerun a `failed` turn within its slot under the same key (ADR 0021 §9). `stale` wakes a Heinzelmännchen when
    its approved output's `valid_until` passed: the approval time plus the output's `valid_for` (ADR 0021 §2), so
    Python, not the model, decides when a truth expires; its key is `<id>@stale:<output>:<valid_until>`, and a turn
    with cause `stale` always renders. An input's `recheck` is only how often it is polled and never a wake by itself
    (20). The `source-changed` key of (5) uses the hash of the watched facet, or the canonical hash of the whole
    fingerprint map when the input names no facet (ADR 0021 §8). Wakes due in the same tick coalesce: `turn.started`
    keeps `cause` (the first, in the order of (4)) and gains the additive field `causes: [...]` with one key per
    cause, so each cause's idempotency holds on its own and old readers still read `cause`; `turn.finished` likewise.
14. **Chat while a turn runs: two queues, both journalled.** pi's agent loop keeps a steering queue (delivered after
    the current tool round) apart from a follow-up queue (consulted only when the run would end); Pi Durable submits
    with a `requestId` and `whenBusy: followUp | steer`
    ([pi agent-core](https://github.com/earendil-works/pi/blob/main/packages/agent/README.md)). hz takes both. Every
    message is first written as a request file `<id>/inbox/<request id>.json` (atomic, idempotent by request id; it
    holds the text, which the spine never does, and expires with the bodies, `retention_days`). A message to an
    `awake` Heinzelmännchen during *gather* or *think* is then recorded as `turn.steered {request_id, hash}` and
    reaches the model before its next request; one that arrives during *act* or *finish*, or that the user marks
    "after this", is recorded as `turn.requested {cause: asked, key}` and becomes the next turn, whose body receives
    the text. Any other wake that finds the Heinzelmännchen `awake` is a `turn.requested` too (Reactor's single
    flight with coalescing): when the turn finishes, the writer starts **one** follow-up turn with all requested
    causes, never two turns in parallel per Heinzelmännchen. The request file exists before the model sees anything,
    so a crash loses no message; the writer (21) appends the spine line at its next step boundary. `hz heinzel stop
    <name>` cancels the running step, finishes the turn `abandoned` with reason `stopped` and keeps queued messages as
    the next turn's input, as pi returns them to the editor.
15. **`skipped` says why, and a slot where nothing moved costs nothing.** The disposition carries `reason:
    nothing-moved | check-unmet | done-when-unmet | cap | throttled` and `moved: [inputs]`; `check-unmet` and
    `throttled` are reserved for the trigger kinds a later ADR records. A `schedule` wake first gathers and
    fingerprints; when no input moved since the last turn and the contract's `hz.render_on_schedule` is `if-moved`
    (ADR 0021 §2: what `hz heinzel new` writes; an absent key means `always`), the tick writes one line
    `turn.skipped {causes, keys, reason: nothing-moved, moved: []}` and never opens a body or calls a model:
    Reactor's zero-cost `skipped` receipt, so the timeline shows "checked at 15:00, nothing moved" rather than
    silence. `turn.skipped` is final for its keys under (5), like a `turn.finished`. A `done-when-unmet` skip has
    spent its model call; only `nothing-moved` is free. Until
    [ADR 0054](0054-observability-is-whether-each-team-promise-is-kept.md) records how a skip counts towards a
    promise, `if-moved` is refused for team contracts (`hz heinzel check`), so no team promise shows `late` for a slot
    that rightly did nothing. A skipped turn is never rerun by the contract's `retries`; the user may still
    `hz heinzel retry` it.
16. **An effect ledger, replay-safe tools, a resume limit.** Every effect writes `effect.started {key, effect}` before
    it is performed and `effect.done {key, hash}` (or `effect.failed {key, error}`) after; the effect's own event
    (`draft.placed`, `question.asked`, a notice) follows with the same key. A resume that finds `started` without an
    outcome does not perform again: for an effect in the world it asks the adapter whether the effect is there by its
    key (ADR 0038 §2's tag lookup), then writes `done` or performs it once; for a local effect (a notice, a question)
    it checks hz's own record. Pydantic AI's step persistence names the same states `started | completed | failed |
    unknown_after_crash` ([Pydantic AI](https://pydantic.dev/docs/ai/harness/step-persistence/)). Every tool declares
    `replay_safe` ([ADR 0050](0050-pydantic-ai-runs-a-turn-a-coding-agent-is-a-handoff.md) §3): reads are, `report`
    is (idempotent by turn and tool call id), `handoff` is not; an interrupted unsafe call is recorded as interrupted
    and the model gets an `interrupted` result instead of a silent rerun, Pi Durable's replay rule. A turn resumed more
    than `[heinzel] max_resumes` times (default 3) is `abandoned` with the step and error, Pi Durable's
    `restartLimit`, so a turn that crashes at the same step every time stops spending ticks; the timeout still applies.
17. **A turn is pinned to what it started with.** `turn.started` records `contract_version`, `skills` (their
    fingerprints, ADR 0050 §7), `lessons_seq` (the spine `seq` of the last lesson event the turn saw) and
    `vocabulary` (the version of the event kinds and step names, 18). A resumed or waiting turn runs under exactly
    those, so "the contract's body at the current version" of (8) means the pinned one; a contract amended while a
    turn waits applies from the next turn. Restate pins an invocation to the deployment that started it and DBOS
    recovers a workflow only under its own application version ([Restate](https://docs.restate.dev/operate/versioning),
    [DBOS](https://docs.dbos.dev/python/tutorials/upgrading-workflows)).
18. **New kinds; the vocabulary is frozen and migrated on read.** Statements 12–16 add `turn.waiting`, `turn.resumed`,
    `turn.steered`, `turn.requested`, `turn.skipped`, `effect.started`, `effect.done` and `effect.failed`. Further:
    `question.asked {key, question}` and `question.answered {key}`: the question is one of the short texts (3) the
    user is meant to read; it is asked once per key, survives a crash between asking and answer, and the answer is an
    input of the next turn, or of the waiting turn whose `on` names it (12); Pi Durable's memos and Pydantic AI's
    deferred requests are the same idea. `context.edit {target, replacement}` (`target` a turn, lesson or body line;
    `replacement` null or a short text) changes what the model sees and never the record, as pi's `context_edit`
    entry; `lesson.dismissed` is still written and the context builder derives its edit from it, while a line the
    user asks hz to hide is a `context.edit` of its own. `usage {kind: summary | lesson | handoff, tokens,
    tokens_cached, usd}` records spend outside a turn's own model calls and counts against the caps beside
    `turn.finished`'s cost, never inside it, so nothing is counted twice; pi's `usage` entries do the same. The event
    kinds and the five step names (*gather*, *think*, *outcome*, *act*, *finish*) are compatibility data, as Pydantic
    AI's durable integrations treat persisted operation names; a test pins them, and readers migrate old kinds on read
    and never rewrite a spine, as pi migrates session files on load.
19. **Summaries have a schema and chain.** `summary.written` holds a fixed set of fields the user reads in the same
    shape every time: the asset, what the user wants, decisions, open questions, the next deadline, the sources
    watched. The previous summary is an input of the next, so summaries chain; `[heinzel] summary_keep_turns`
    (default 5) recent turns stay verbatim beside it, pi's `keepRecentTokens` counted in turns; and when a body is
    rendered into a turn's context, the cut never separates a tool call from its result
    ([pi compaction](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/compaction.md)).
20. **The tick as a deterministic gateway.** Reactor's gateway stages an arrival before the wake and keeps a cursor
    per source ([Reactor spec](https://github.com/openprose/reactor/blob/main/spec/02-ReactorHarness.md)); hz's tick
    does the same in a **source cache**, `<data dir>/heinzel/sources/`: per `(capability, method, args, window)` the
    fetched result, its fingerprints (per facet, ADR 0021 §8), cursor and `as_of`, written **before** any
    `turn.started`. A crash between fetch and wake re-wakes from the cached input and never fetches twice; *gather*
    reads the cached input when its fingerprint matches. The cache is not the index and not authoritative: losing it
    costs a refetch; it is private, holds content, and is pruned by `heinzel:prune` under `retention_days` with ADR
    0021 §13's rules. Each input is polled when its recheck is due (`inputs.<name>.recheck`, default every tick; a
    source several inputs share is polled at the shortest), not every input every tick. Wakes in one tick are ordered
    by the declared graph's topological height (ADR 0052), then oldest due, under `max_wakes_per_tick`, so a diamond
    upstream renders before its readers, and a reader wakes on an approval only when the approved text's material
    fingerprint moved (ADR 0052 §7). Cost is rolled up by cause: `turn.finished` records `tokens_cached` beside
    tokens and USD, a turn with several causes counts under its first, and `hz heinzel show` says "3.10 USD this
    month: 2.40 schedule, 0.70 asked".
21. **One writer per spine.** Appending needs the spine's lock (an exclusive `flock` on `<id>/lock`, taken after the
    daemon's tick lock of [ADR 0020](0020-daemon-head.md) when the caller is a tick): whichever hz process ticks
    (`hz system daemon tick` from launchd or systemd, or `hz system serve daemon`) or serves a chat turn from any head
    takes it, and only one holds it. A web chat turn may take the lock; the web head never ticks (ADR 0020 §1, ADR
    0039). Spines are not shared between machines: a chat turn is served on the machine that holds the session, and
    for a team Heinzelmännchen which daemon runs its wakes is still the lease of (10). Everything else reads the index
    and tails the spine; a page or chat that opens mid-turn gets the current row and then the new lines, never a
    replay, Pi Durable's `watch()` contract.

**Worked example: Priya's weekly report.** Priya is an engineering manager at Acme; her team is Checkout. (Tests
later: `tests/e2e/priya_test.py` replays this timeline with a test model and test-only adapters.)

| When | Cause | What happens in the session |
| --- | --- | --- |
| Mon | `created` | In chat: "every Friday at three, draft this week's section of our weekly report from merged PRs, the board and my calendar; I approve." The turn compiles `weekly-report` v1 from the sentence and the org playbook's template: inputs `code-host.merged_pull_requests`, `tracker.issues_updated`, `calendar.events`, output `section`, publishes `documents.place_draft` into the report document, approvers `[owner]`, `schedule: "0 15 * * FRI"`, `max_usd: 0.5`. State `proposed`; `hz approve` makes it v1 and `asleep`. |
| Fri 15:00 | `schedule`, key `hz-01J…@2026-10-30T15:00` | Gather: three inputs, hashed. Think: one model turn, a `last_approved` call (none yet). Outcome: a draft. Act: `place_draft` with key `hz-01J…@v1@2026-10-30T15:00@doc:checkout/weekly-report`; the section lands under the tag "🧝 DRAFT (weekly-report v1): edit, then delete this line to approve". One notice. `turn.finished drafted 0.31 USD`; state `waiting`. |
| Fri 15:02 | (crash) | Priya's lid closes between `draft.placed` and `turn.finished`. At 15:40 the tick finds the open turn by its key, sees `draft.placed`, writes `turn.finished` and spends nothing. The adapter would have found the tag by its key anyway. *Amended 2026-10-24:* the ledger (16) has `effect.done`, so no adapter is asked. |
| Fri 15:02, variant | (crash, amended 2026-10-24) | The variant the ledger exists for: the lid closes after `effect.started` and before `effect.done`. The tick finds `started` without `done`, asks the adapter for a tag with the key, finds it, writes `effect.done` and `draft.placed`, then `turn.finished`: no second section, no second spend. |
| Mon | `approval` | The tick's `draft_state` says the tag is gone, approved by Priya (revision history). Think: draft vs approved. Outcome: two lessons: "risks go above the summary", "name the epic, not the ticket numbers". `lesson.recorded` ×2, `approval.observed` (share changed 23 %). Priya reads them under "what I learned from you" and dismisses nothing. |
| Tue | `asked` | Priya, in the sidebar chat with `weekly-report` selected: "also mention incidents from the on-call channel." Outcome: an amendment adding input `incidents: chat.messages` and `chat:read` to `requires`: it *widens*, so the compiler marks it as needing her approval; `hz approve` makes it v2. |
| Fri | `schedule` | The draft records v2 and carries both lessons. The tag says "v2". |
| Wed | `discovered` | The daily discovery finds a new "Checkout incidents" document the playbook's hint matches. The turn proposes an amendment to read it instead of the channel; Priya declines in chat (`amendment.declined`); the Heinzelmännchen won't propose that source again. |
| Fri 15:00, later | `asked` while `awake` (amended 2026-10-24) | During *think* of a later Friday turn Priya writes in the sidebar "keep it under ten lines": `turn.steered`, and the model sees it before its next request (14). A tracker change seen in the same minute is a `turn.requested` and runs as one follow-up turn after this one. |
| Any time | | `hz heinzel list --artifact doc:checkout/weekly-report` shows it among Priya's 140 Heinzelmännchen in milliseconds; `hz heinzel show weekly-report` shows v1, v2, seven turns, two lessons and 2.1 USD this month. |

### Consequences

- Good: a Heinzelmännchen remembers, explains itself and improves, and the user can read every step of that in a text
  file hz owns; switching the model or the runner loses nothing.
- Good: crashes, sleep and double ticks are handled by construction (keys and checkpoints), which is what the thin
  slice's three-Friday test needs most.
- Good: hundreds are cheap: the index answers the tick and the list, a source is fetched once per tick, and spending is
  capped per turn, per day and per user.
- Good: lineage without content leaves the machine, as before; the never-commit list holds.
- Bad: hz now owns pruning, summarising and context building that a vendor's session would have done for us; the rules
  are deterministic and tested, but they are ours to get right (8).
- Bad: two stores, a spine and an index, and the reindex that keeps them honest; the index is derived to make that safe.
- Bad: ADR 0038 §4's "diff in the packet" becomes lessons the model decides, so a wrong lesson can steer later drafts;
  the user sees and dismisses lessons, and a contract's `### Done when` still gates every draft.
- Bad: amends five records (below); the `workflow` module is renamed and migrated (ADR 0051); the turn engine changes
  (ADR 0050).
- Neutral: the maintainer's "Pi Durable" alternative is answered by borrowing its rules; a `heinzel-pi` runner adapter
  remains possible as a handoff target (ADR 0050).
- Bad (amended 2026-10-24): the vocabulary grows (waiting, steering, requests, the ledger, questions, context edits,
  usage), and each kind is a compatibility promise, which is why a test freezes it (18).
- Good (amended 2026-10-24): a scheduled slot where nothing moved costs one spine line and no model call (15), and
  wakes that pile up during a turn become one follow-up turn (14).

### Confirmation

Not yet: proposed. When implemented:

- `tests/unit/kernel/session_store_test.py`: append with fsync and chain, a truncated or edited line detected and
  contained, unknown kinds skipped, the index rebuilt from spines equal to a direct scan, 500 spines listed in under
  50 ms, a process killed mid-append leaving a readable chain (`tests/integration/session_store_test.py`).
- `tests/unit/modules/heinzel_turns_test.py`: every cause's key, resume at each checkpoint with no second spend or
  second `place_draft`, `abandoned` never resumed, caps per turn, per day and per user, `max_wakes_per_tick`, one fetch
  per source per tick (amended 2026-10-24: per input at its recheck, from the source cache, statement 20), the context
  rule of (8) and summaries at the budget, bodies expired without loss of continuity.
- `tests/unit/modules/heinzel_contract_test.py`: versions, `HZC050` on a widening amendment the Heinzelmännchen
  proposes and acceptance of the same text from the user, drafts recording their version, lessons never in the contract.
- `tests/unit/modules/heinzel_lessons_test.py`: lessons from a replayed approval, dismissal, the approver and share
  recorded without text; no per-person aggregation anywhere (`tests/unit/repository_test.py`, the ADR 0024 rule).
- `tests/integration/heinzel_test.py`: `hz heinzel list|show|chat|wake|new`, `hz drafts`, `hz approve` through the
  CLI and MCP, the web endpoints behind the token, nothing on the unauthenticated REST head.
- `tests/e2e/priya_test.py`: the worked example above, with a test model and test-only adapters, including the crash
  between `draft.placed` and `turn.finished`.
- A test that no event kind in the spine carries a field named `text`, `draft`, `transcript` or an email address
  (`tests/unit/modules/heinzel_spine_test.py`), the same lint `hz/state` will enforce.
- Amended 2026-10-24, for statements 12–21: a frozen-vocabulary test of event kinds and step names, with old kinds
  migrated on read and the spine file unchanged (`tests/unit/modules/heinzel_spine_test.py`); a `waiting` turn survives
  a daemon restart and resumes on the right wake with `woken_by`, and `until` wins over an `on` that never matches;
  steering during *think* appears in the model's next request and in the spine, a message during *act* becomes the next
  turn, and wakes while `awake` coalesce into one follow-up turn with all causes; `retry` leaves two bodies and one
  superseded draft; the ledger resolves `started` without `done` by asking the adapter, with zero second writes in the
  three-Friday test; an unsafe tool interrupted by a crash yields an `interrupted` result and no rerun; `max_resumes`
  ends in `abandoned`; a resumed turn uses its pinned contract version, skills and lessons; a schedule slot with nothing
  moved writes one `turn.skipped` and calls no model; staged inputs are not fetched again after a crash; wakes are
  ordered by height; a second process can't append while the lock is held and its message arrives through the inbox
  once; a question is asked once per key and its answer resumes the waiting turn; `context.edit` hides a line from the
  model and leaves the body unchanged; the summary has the fixed fields, chains the previous one and keeps
  `summary_keep_turns` turns verbatim (`tests/unit/modules/heinzel_turns_test.py`, `tests/e2e/priya_test.py`).

## More Information

- Decided by the maintainer, 2026-10-24: durable agents with one session each, one contract in versions, amendments,
  lessons shown to the user, hundreds per user, no central server, "hz owns the session". Technical shape by the
  architect.
- **Amends** [ADR 0010](0010-towards-a-reactive-workflow-engine.md) ("one bounded agent session per render" becomes one
  bounded *turn* of a continuous session); [ADR 0020](0020-daemon-head.md) §4 and §8 (the tick wakes Heinzelmännchen
  by key; a compiled job's receipt is a turn's receipt-grade event; team state on the `hz/state` branch with leases per
  Heinzelmännchen, as the "core and skills" note recommended); [ADR 0021](0021-workflow-contracts.md) §6 and §13 (a
  contract belongs to one Heinzelmännchen and has versions; the `.record.json` becomes the turn body with the same
  retention); [ADR 0029](0029-agent-runners-are-capability-adapters.md) (the runner no longer owns the session;
  ADR 0050 says what runs a turn); [ADR 0038](0038-drafts-are-approved-in-place.md) §4 and §8 (lessons; `hz drafts`
  and `hz approve` are the view); [ADR 0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md) (the
  sidebar chat with a Heinzelmännchen selected is its session; the general chat stays).
- **Reconciled with the prior notes** (scratchpad, 2026-10-24). *Core and skills*: its rename to `hz skill` and the
  `SKILL.md` directory as the one file format are **not** adopted; the maintainer chose "Heinzelmännchen" and kept
  "contract" and its `.prose.md` text, and advice skills feed a turn as ADR 0050 says. Its state layout (orphan branch,
  leases, owner-then-delegate, the never-commit list) is adopted as (10). *Research note*: idea 2 (durable runs,
  idempotent writes) is (5); idea 1 (the inbox as a projection) is (11); early cutoff on the approved output's facets
  waits for output facets (ADR 0021 §8, later; amended 2026-10-24: the cutoff on the whole approved output is decided in
  ADR 0052 §7, per facet still later); the Pi Durable verdict ("borrow the concepts, don't adopt the package") stands.
- **Amended 2026-10-24** (statements 12–21, the Considered Options' Pi Durable attribution and the OpenProse Reactor
  option): sources as read that day: [earendil-works/pi](https://github.com/earendil-works/pi) (the agent loop, the
  session format, compaction, Pi Durable's README and spec), [Pi Durable](https://earendil.com/posts/pi-durable/),
  [Temporal](https://docs.temporal.io/workflow-definition), [Restate](https://docs.restate.dev/concepts/durable_execution),
  [DBOS](https://docs.dbos.dev/python/tutorials/workflow-tutorial),
  [Inngest](https://www.inngest.com/docs/features/inngest-functions/steps-workflows),
  [Cloudflare Agents](https://developers.cloudflare.com/agents/api-reference/schedule-tasks/),
  [LangGraph](https://docs.langchain.com/oss/python/langgraph/persistence),
  [Pydantic AI durable execution](https://pydantic.dev/docs/ai/integrations/durable_execution/overview/),
  [OpenProse Reactor](https://github.com/openprose/reactor) and [OpenProse](https://github.com/openprose/prose).
  Rejected from them: replaying *code* (Temporal, DBOS, Inngest), since a turn is five named steps whose only
  non-deterministic step is rerun from the last recorded message; Continue-As-New, since bodies expire and the spine
  stays small; undo by popping the last exchange, since the spine is append-only (`retry` and `context.edit` are the
  user-facing equivalents); a memory the model edits through a tool, since that would be a permission it grants
  itself. Amends [ADR 0028](0028-integrations-are-capability-adapters.md) (fingerprints per facet) and
  [ADR 0052](0052-the-graph-emerges-from-recorded-reads-and-writes.md) §7 (downstream wakes only when the approved
  output moved); [ADR 0021](0021-workflow-contracts.md) §2 and §8 gain the keys and facets these use.
- **Open:** whether a retired Heinzelmännchen's spine should ever be deleted (recommendation: only by the user, with
  `hz heinzel forget`, and its receipt-grade events stay in `hz/state`); whether lessons may be shared with a team's
  Heinzelmännchen of the same contract (recommendation: no; they are about one user's edits); the exact id prefix.
  Added 2026-10-24: how a session follows its user across their own machines, given one writer per spine and spines
  that aren't shared (statement 21; recommendation: the machine running the daemon holds the session, others submit
  through the web head paired to it, ADR 0046); when ADR 0054 counts a `nothing-moved` skip as a kept promise, so
  team contracts may use `if-moved` (statement 15).

**Staged plan.** PR-sized steps, each with its acceptance criterion; (1) and (2) can run in parallel with ADR 0051's
rename, which must land before (3).

| # | Step | Acceptance |
| --- | --- | --- |
| 1 | Kernel `SessionStore` primitive: append-only JSONL with chain and fsync, turn bodies, the derived SQLite index with `seq` watermark and reindex | The unit and integration tests of the first Confirmation bullet pass; nothing in the kernel names a Heinzelmännchen |
| 2 | The `heinzel` module (ADR 0051): ids, names, states, the contract in versions, `hz heinzel list \| show \| new`, `hz heinzel migrate` for today's contracts and records | Every existing `.prose.md` becomes a Heinzelmännchen at v1 `asleep`; `list` filters by artifact, state and contract; `migrate` twice changes nothing |
| 3 | Turns with Pydantic AI (ADR 0050) for causes `schedule`, `asked` and `created`; drafts as local files; `hz heinzel chat \| wake` | The weekly-digest fixture drafts on the daemon and from chat into the same session; a killed process resumes without a second spend |
| 4 | `hz drafts`, `hz approve` on local drafts, then in place through the first `write` adapter (ADR 0038); drafts record their contract version; the `approval` turn and lessons | Approving yields `approval.observed` and lessons; a dismissed lesson leaves the prompt; the tag carries id and version |
| 5 | Amendments: proposed by the Heinzelmännchen or the user, `HZC050`, versions, `hz approve` of amendments and of proposed Heinzelmännchen | Priya's v2 flows end to end; a widening amendment from the model is refused and the same text from the user accepted |
| 6 | Causes `source-changed` and `discovered`: fingerprints per source, one fetch per source per tick (amended 2026-10-24: per facet, staged before the wake, each input at its recheck, wakes ordered by height, statement 20), `max_wakes_per_tick`, the daily caps | 200 asleep Heinzelmännchen cost one index query per tick; an input that moved wakes exactly its watchers |
| 7 | Handoff to a coding agent (ADR 0050) and what of its transcript is kept | A turn hands a task to the bound runner and continues with its report; the spine holds hashes and cost, the body the transcript |
| 8 | Team state: receipt-grade events to `hz/state`, leases per Heinzelmännchen, owner-then-delegate | Two daemons against one Acme team repository run a team Heinzelmännchen once per slot; `main` stays protected; the lint refuses text |
| 9 | Web: "My Heinzelmännchen", the page per Heinzelmännchen, the sidebar chat as its session | The page shows versions, timeline, lessons and the pending item; the sidebar message appears in `hz heinzel show` |
| 10 | `tests/e2e/priya_test.py` | The worked example passes with a test model and test-only adapters |
| 11 | The action log (ADR 0052): hz-written entries from turn steps, the `report` tool and `hz heinzel report`, the lint; `hz graph` from logs and contracts with filters and `--json` | The Priya fixture's graph shows `weekly-report` reading the tracker and writing the report document, declared and observed apart; golden JSON |
| 12 | The graph Heinzelmännchen (aliases) and the team graph from `hz/state` metadata | Two users' daemons yield one team graph on each machine; another user's unreadable artifact shows as a name |
| 13 | The flow (ADR 0052, once confirmed): `needed_by` upstream, repeated nudges, fallback with `fell_back`, declared escalation | Theo's `my-row` unapproved at Friday noon: Priya's report proceeds stale and says so; nobody is told but the two users |
