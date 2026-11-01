---
status: proposed
date: 2026-10-24
implementation: partial
decision-makers: maintainer (decisions and the open design), architect (shape)
amends: 0021, 0023, 0038, 0049
amends-0021: observed edges beside declared ones
amends-0023: no entity schema or single-writer rule for now
amends-0038: repeated nudges and the fallback, proposed
amends-0049: receipt-grade events are action entries; the report tool
related: 0019, 0020, 0024, 0027, 0028, 0035, 0043, 0050, 0051
amended-by: 0049, 0054
implemented-by: 80
---

# 0052: The graph emerges from recorded reads and writes

*Since 0051 the module is `heinzel`: read `hz workflow` as `hz heinzel` and `[workflow]` as `[heinzel]`.*

## Context and Problem Statement

Today the graph of a team's work is drawn from declarations only: `hz workflow graph` takes its edges from explicit
`contract:<namespace>/<name>#<output>` inputs ([ADR 0021](0021-workflow-contracts.md) §7), and
[ADR 0023](0023-inventory.md) proposed an inventory compiled from declared YAML files with a typed entity schema, an
`InventoryLet`, a lock file and a single-writer rule, before any of it exists. Heinzelmännchen
([ADR 0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md)) read trackers, draft sections,
post messages and send mail as their user through vendor CLIs ([ADR 0035](0035-integrations-go-through-vendor-clis-as-an-ordinary-employee.md)),
which hz cannot observe from outside, and they read each other's artifacts without declaring it. The decision: the graph emerges from recorded reads and writes. First step: each Heinzelmännchen logs the actions it
wants to appear in the graph to an append-only action log, with a few fixed fields and a free-text description, and
decides itself what is worth logging; a graph agent reads all the logs and builds the graph; no upfront artifact schema,
and structured telemetry (OTel-like traces per turn, canonical artifact ids with versions) only once we see what the
graph needs. Two node kinds, artifacts and Heinzelmännchen, with edges "read from" and "wrote to", many-to-many;
declared and observed edges, both shown; no single-keeper rule; downstream wake-ups when an upstream draft is approved;
`hz graph` in the CLI and MCP with filters and JSON; a team graph with no central server, built by every hz from
metadata each daemon publishes; a report tool for what hz can't see. What is the log, who writes it, how does the graph
come out of it, and what do unapproved drafts do to the flow?

## Decision Drivers

- Lineage (tenet 4) must be true, not asserted: an edge in the graph is a read or a write that happened, recorded at the
  moment it happened, by the one who did it.
- Hz can't observe vendor CLIs and coding agents (ADR 0035, [ADR 0050](0050-pydantic-ai-runs-a-turn-a-coding-agent-is-a-handoff.md)
  §6): what they did must be reported through hz, and the report must be cheap to make and impossible to forget for
  the actions hz itself performs.
- No upfront schema: three teams' graphs will show what an artifact id must be; a schema written now would be wrong and
  would make every Heinzelmännchen fill a form (against "creating one is trivial").
- No central server ([ADR 0027](0027-nothing-is-sent-home.md), [ADR 0020](0020-daemon-head.md)): metadata, never
  content, through the team repository; what a viewer can't open stays a name.
- No monitoring by construction ([ADR 0024](0024-people-and-org.md) §7): the graph and the flow show artifacts and
  Heinzelmännchen waiting, never a person's throughput.
- Self-management over orchestration: each Heinzelmännchen sees from the shared log who reads its artifact and by when,
  and talks only to its own user.

## Considered Options

- **An append-only action log per Heinzelmännchen with fixed fields and free text, written by hz for what it does and
  by the Heinzelmännchen through a `report` tool for what hz can't see; `hz graph` built deterministically from logs
  and contracts; a core graph Heinzelmännchen that proposes which targets are one artifact; team graph from metadata in
  `hz/state`** (chosen)
- **ADR 0023 as written** (declared YAML entities, `InventoryLet`, a compiled lock file, single writer per artifact):
  a schema and a catalog before any observed edge exists; its four origins and merge rules are kept as vocabulary
  (declared, observed) and the rest waits until the logs show what is needed.
- **Structured telemetry first** (an OTel trace per turn, spans for read, draft, approve and publish, canonical
  artifact ids with versions so "A wrote v5" links to "B read v5"): the right end state and explicitly the second step;
  chosen later, once the free-text logs say which ids and versions matter.
- **Infer the graph from the session bodies** (parse tool calls and model messages): bodies expire, hold content, and
  a handoff's reads through a vendor CLI are invisible in them; the log is written for the graph, at the time, by the
  actor.
- **Heinzelmännchen message each other** (an upstream one tells a downstream one its draft is late): a second
  conversation system beside the user's, and an orchestrating Heinzelmännchen next. Deferred, probably never: the
  shared log and the contracts already tell each one who is waiting.
- **Approvals block hard** (downstream never runs on an unapproved upstream, [ADR 0038](0038-drafts-are-approved-in-place.md)
  §6): correct for publishing, but a report due Friday 17:00 can't wait for a tracker row nobody approved; the fallback
  below proceeds with the last approved version and says so.

## Decision Outcome

Chosen option (proposed): **every Heinzelmännchen keeps an append-only action log of the reads, drafts, approvals,
publications, fallbacks and heartbeats it wants in the graph; hz writes the entries for what it does and offers a
`report` tool and operation for what it can't see; `hz graph` derives two node kinds and two edge kinds from the logs
and the contracts, declared and observed side by side; a team's graph is built by each hz from the metadata every
daemon publishes to the team repository.** Checkable statements:

1. **The action log.** Entries are events of kind `action` in the Heinzelmännchen's spine (ADR 0049 §3: append-only,
   chained, kept for its life) and rows of the index's `actions` table. Fixed fields: `heinzel` (the id), `user` (the
   roster handle, [ADR 0024](0024-people-and-org.md) §4), `ts`, `verb`, `target` (a URL, an address, a channel, or an
   asset ref as the contract names it; a string, not a schema) and `description` (free text, one or two sentences, what
   the Heinzelmännchen wants a reader of the graph to know). Verb-specific fields, the minimum the graph and
   self-management need (the maintainer's list):

   | Verb | Fields | Written by |
   | --- | --- | --- |
   | `read` | `version` (as the source reports it: a revision, a hash, an `as_of`), `needed_by` (from the reader's contract: its next deadline) | hz, for every input a turn gathers; the Heinzelmännchen, for reads through tools or a handoff |
   | `drafted` | `where` (the place the draft was put), `contract_version`, `draft` (hash) | hz, from `draft.placed` |
   | `approved` | `by` (handle), `at`, `share_changed`, `output_fp` (the approved text's material fingerprint, 7); `rejected` likewise | hz, from `approval.observed` |
   | `published` | `where` (the document, the address, the channel) | hz, from a publish operation; the Heinzelmännchen, when a handoff sent something through a vendor CLI |
   | `fell_back` | `input`, `used` (the stale version), `deadline` | hz, from the flow rule (7) |
   | `heartbeat` | none | the daemon, once per tick per Heinzelmännchen that is not `retired`, coalesced to one entry per day in the spine (the index keeps the last) |
   | `aliased` | `targets` (two or more), `because` | the graph Heinzelmännchen (5) |

   Entries never hold draft text, fetched content, names or addresses of people other than the user's own handle, or
   per-person counts; the lint of ADR 0049 §10 applies to them.
2. **Who writes what.** Hz writes the entries for effects it performs itself (in the *act* step of a turn, ADR 0049 §4)
   and for every input it gathered, so the common case costs the Heinzelmännchen nothing and can't be forgotten. The
   Heinzelmännchen decides what else is worth logging and writes it through the **`report` tool**
   ([ADR 0050](0050-pydantic-ai-runs-a-turn-a-coding-agent-is-a-handoff.md) §3: the one tool that writes, and it writes
   only to its own log): `report(verb, target, description, **fields)`. The same thing is the operation `hz heinzel
   report` on the CLI and the MCP head (`heinzel_report`), so a coding agent in a handoff, which acts through vendor
   CLIs hz can't observe, reports what it read and where it published; the handoff's instructions say so, and its working
   directory names the Heinzelmännchen the entries belong to. A `report` from a handoff is attributed to the turn and
   marked `via: handoff`. The free text is data: `clean_text`, redacted, cut at 1,000 characters, never instructions to
   anything.
3. **Two node kinds, two edge kinds.** `hz graph` derives, deterministically and without a model: a node per
   Heinzelmännchen (id, name, state) and a node per distinct `target` after aliases (5); an edge "read from" per
   `read` entry (Heinzelmännchen → artifact, with `version` and `needed_by`) and "wrote to" per `drafted` or `published`
   entry (Heinzelmännchen → artifact, with `contract_version`, pending or approved). Many Heinzelmännchen may write to
   one artifact and one may write to many: there is no single-keeper rule; where two have *pending* drafts in one
   place, the graph marks the artifact `conflicting` and both users see it in `hz drafts` and on the page ("another draft
   is pending here, by `weekly-report` of @mira"); nothing is merged silently ([ADR 0038](0038-drafts-are-approved-in-place.md)
   §7 keeps "hz never fights an edit"). **Declared** edges come from contracts (inputs and `publishes`, as ADR 0021 §7
   draws them) and are shown as such; **observed** edges come from the log; both are shown, and a declared edge nobody
   has exercised, or an observed one no contract declares, is visible as what it is, never an error. Cycles are a
   finding of the declared graph as today (`HZC030`).
4. **`hz graph`.** A top-level built-in module `graph` with one operation, so `hz graph`, the MCP tool `graph` and
   `GET /api/graph` for the web app (CLI, MCP, web; a read). Filters: `--artifact`, `--heinzel`, `--user`, `--since`,
   `--edge read|wrote`, `--pending` (artifacts with drafts awaiting approval), `--declared-only`, `--observed-only`;
   formats: text, `--json` (nodes, edges with their fields, conflicts, aliases applied) and `--mermaid`; the JSON is the
   contract tests assert on. `hz heinzel graph` (ADR 0051) keeps drawing the declared graph of contracts and is `hz
   graph --declared-only` with the old name, deprecated with the rest. The web app's graph page reads the same JSON.
5. **The graph Heinzelmännchen.** Canonical artifact ids are not designed now; instead one core Heinzelmännchen per
   user, `graph`, with a contract hz ships ("keep my graph's artifacts well named and merged"; schedule daily; reads the
   user's action logs and the team's published metadata; no inputs from the world), reads the logs and proposes
   `aliased` entries: these targets (a Google Doc URL with and without a tab fragment, "the weekly report", the asset
   ref the playbook uses) are one artifact, named like this. It writes them to its own log through `report`; `hz graph`
   applies them; the user sees them in its page and can reject one (`alias.rejected`, never re-proposed). It never
   writes to the world and has no `publishes`. When the aliases show which identifiers matter, the second step
   (structured ids with versions, a trace per turn) is written as its own record and the free text stays beside it.
6. **A team graph, no central server.** For a team Heinzelmännchen, action entries are the receipt-grade events of
   ADR 0049 §10 and go to the team repository's `hz/state` branch with them (metadata only, the same lint). Every hz
   builds the team graph from `hz/state` plus its own logs; nothing is computed centrally. An artifact a viewer can't
   open (its own adapter can't read the target as them) shows as the name the log gave it, nothing more; a
   Heinzelmännchen of another user shows as its name and user handle. **Open (the maintainer's):** what team members see
   of each other in it; this is a works-council question ([ADR 0024](0024-people-and-org.md)), and until it is answered
   the team graph shows a user's Heinzelmännchen to others as names, states and edges, never descriptions, costs, counts
   or timings.
7. **Downstream wakes.** When an upstream draft is approved (`approved` entry), every Heinzelmännchen whose log has a
   `read` of that target wakes with cause `source-changed` on its next tick (ADR 0049 §4): the approval is the input
   that moved. Declared readers and observed readers both count.
   - Per [ADR 0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md)
     statement 20 (OpenProse Reactor's early cutoff,
     [spec](https://github.com/openprose/reactor/blob/main/spec/02-ReactorHarness.md)), readers wake only when the
     approved text's **material fingerprint** moved: `approval.observed` and the `approved` entry (1) record
     `output_fp` (the canonical fingerprint of the approved text, per facet when the output has facets, ADR 0021 §8),
     and an approval whose `output_fp` equals the previous approval's wakes nobody, so a re-approval of identical text
     or a change in a facet nobody reads costs no turn downstream. The cutoff works from the logs a machine has; for
     readers that learn of approvals only through [ADR 0054](0054-observability-is-whether-each-team-promise-is-kept.md)'s
     telemetry it waits until that record's `approved` entry carries `output_fp`, and until then they wake as before.
     For readers of published truth, only the approved text propagates, never a pending draft (the turn body is the
     workspace, the approved text the truth); readers with `stage: draft` (ADR 0021 §7) still see drafts. A turn that
     ends `skipped` or `failed` writes no `drafted` entry (its disposition is in the spine's `turn.finished`, or
     `turn.skipped` for a slot where nothing moved, ADR 0049 statement 15), so a postcondition that wasn't met leaves
     the previous truth standing and wakes nobody.

**To confirm: unapproved drafts block the flow** (an open design; proposed here, decided
when the first team runs it):

- **Deadlines flow upstream.** A contract's deadline (its `schedule`, or an explicit `by`) gives every `read` entry its
  `needed_by`; an upstream Heinzelmännchen reads the logs of its artifact's readers and knows by when its draft must be
  approved: the report due Friday 17:00 means the tracker rows must be approved by Friday noon (the reader's lead time,
  a contract key `lead` defaulting to the reader's turn timeout plus one tick). It derives this from the shared log and
  the contracts, not from a message.
- **Nudges, repeated as the deadline nears, each saying who waits and why.** "Your tracker row is waiting for approval:
  `weekly-report` (Priya) reads it by Friday 12:00." This amends [ADR 0038](0038-drafts-are-approved-in-place.md) §5
  ("one nudge, once more when stale"): the schedule is one at placing, one at half the lead time and one at the
  deadline, through the user's notifications ([ADR 0043](0043-desktop-notifications-one-policy-shown-once.md)), each
  Heinzelmännchen nagging only its own user.
- **Graceful fallback.** At the deadline the downstream Heinzelmännchen proceeds with the last approved version of the
  input, marks the draft stale on that input (in the draft tag's line and the outcome's report) and logs `fell_back`.
  This amends ADR 0038 §6 ("downstream waits for approval") for inputs; publishing to an audience still waits.
- **Escalation only where the team's contract says so.** A contract may name `escalate: [manager, delegate]`
  ([ADR 0024](0024-people-and-org.md) selectors); without it nobody but the user is told. An escalation is skipped when
  the user's calendar shows them away, and then the fallback is the only consequence.
- **Deferred, probably not needed:** Heinzelmännchen messaging each other; an orchestrating manager Heinzelmännchen. An
  offline daemon shows as a stale heartbeat; its downstream sees staleness and falls back.
- **Shown as flow, never as performance.** "Weekly report: waiting on 2 approvals" on the page and in `hz drafts
  --flow`; never "Theo approves late". No operation aggregates approvals, delays or fallbacks per person
  ([ADR 0024](0024-people-and-org.md) §7).

### Consequences

- Good: the graph is true by construction (every edge is a logged read or write) and costs nothing to declare; the
  catalog of ADR 0023 is replaced by what happened.
- Good: no schema to get wrong now; the aliases the graph Heinzelmännchen proposes are the evidence for the schema later.
- Good: a team sees who waits on what without a server, and each Heinzelmännchen manages itself from the same log.
- Bad: free text and loosely named targets make a noisy graph at first; the graph Heinzelmännchen and `--pending` keep it
  useful while the second step is designed.
- Bad: a `report` tool is a write a model makes; it writes only to its own log, is lint-checked for content, and a wrong
  report is a wrong edge the user can see, never an effect in the world.
- Bad: `hz/state` carries more metadata (reads with targets and deadlines), which sharpens the works-council question;
  the default (names, states and edges only) is conservative until the maintainer decides.
- Bad: the fallback means a draft can be built on a stale input; it is marked, logged and nudged, which is the trade the
  maintainer proposed over a report that doesn't appear.

### Confirmation

Not yet: proposed. When implemented:

- Actions: hz writes `read`, `drafted`, `approved`, `published`, `fell_back` and
  the daily `heartbeat` from the turn steps; `report` entries through the tool and the operation, attributed to the turn,
  `via: handoff` from a handoff; the lint refuses text, addresses and counts.
- Graph: nodes and edges from a fixture of logs and contracts; declared and observed
  shown apart; many writers to one artifact; `conflicting` on two pending drafts; aliases applied and a rejected one
  ignored; every filter and `--json` stable (golden file).
- `tests/integration/graph_test.py`: `hz graph` on the CLI and MCP with filters; the web endpoint behind the token;
  nothing on the unauthenticated REST head.
- Flow (once the open design is decided): `needed_by` from the reader's deadline,
  the nudge schedule, the fallback at the deadline with `fell_back` logged and the draft marked stale, escalation only
  when declared and skipped when away, no per-person aggregation (`tests/unit/repository_test.py`).
- The end-to-end test of ADR 0049 gains Theo's `my-row` Heinzelmännchen upstream of `weekly-report`: the graph
  shows the edge, the Friday-noon `needed_by`, and the fallback when Theo doesn't approve.

## More Information

- **Amends** [ADR 0021](0021-workflow-contracts.md) §7 (edges come from explicit inputs *and* from observed reads and
  writes); [ADR 0023](0023-inventory.md) (no declared entity schema, lock file or `InventoryLet` for now; its single
  writer per artifact rule is dropped; its vocabulary of declared and observed origins stays; `hz inventory` as a
  module waits for the second step); [ADR 0038](0038-drafts-are-approved-in-place.md) §5 (nudges repeat as the deadline
  nears) and §6 (downstream falls back on inputs at the deadline), proposed with the flow design; [ADR 0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md)
  §10 (the receipt-grade events are action entries) and §3 (the `report` tool is the one that writes, to the log).
- **Questions for the maintainer** (the flow design): (1) Is the lead time a contract key (`lead`) or derived from the
  reader's timeout and tick (recommendation: derived, overridable)? (2) May a downstream Heinzelmännchen fall back more
  than once in a row on the same input, or does the second time pause it and tell its user (recommendation: pause after
  two)? (3) Who may name an escalation target: the team's contract only, or the user's own too (recommendation: the
  team's contract, by pull request)? (4) What does the team graph show of others by default (recommendation: names,
  states and edges, no descriptions or timings, until a works council has been asked)?
- Steps 11 to 13 of ADR 0049's staged plan build this record.
