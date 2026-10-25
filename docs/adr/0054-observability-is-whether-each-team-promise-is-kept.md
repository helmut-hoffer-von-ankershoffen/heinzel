---
status: proposed
date: 2026-10-24
decision-makers: maintainer (the decisions), architect (shape)
amends: 0037, 0052
amended-by: 0081
related: 0021, 0024, 0028, 0039, 0040, 0045, 0049
---

# 0054: Observability is whether each team promise is kept: telemetry in, a graph out

## Context and Problem Statement

A team's work flows through artifacts its members' Heinzelmännchen keep: Costas's and Priya's rows of the tracker every
Wednesday, Mira's weekly report every Friday from those rows. [ADR 0052](0052-the-graph-emerges-from-recorded-reads-and-writes.md)
decided that the graph of this work emerges from what each Heinzelmännchen records, on top of the durable sessions of
[ADR 0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md), which are not built yet. The
maintainer decided (2026-10-24) what to build now, before them: observability means **is each promise kept**, for the
team layer only, like SLOs; a built-in module `telemetry` (after OpenTelemetry) where Heinzelmännchen log what they did
(`hz report`, an append-only log per Heinzelmännchen, synced into the team repository); a built-in module `graph` that
computes each promise's state from the team's contracts and those logs (`hz graph`, JSON Graph Format); and a graph
keeper that is just another Heinzelmännchen, shipped as a playbook. What exactly is a promise and its state, what may an
entry hold, and how do the logs reach the team?

## Decision Drivers

- The team layer only. Sessions, lessons, personal amendments and edit sizes ([ADR 0021](0021-workflow-contracts.md)'s
  success test) are the personal layer and invisible to others by design; a telemetry entry is metadata, never content.
- Flow, never performance ([ADR 0024](0024-people-and-org.md)): the team view counts promises kept per week and says
  where the flow waits ("weekly report waiting on 2 updates"); nothing aggregates anything per person.
- Deterministic and testable without a model or a network: plain code builds the graph, and tests assert on
  `hz graph --json`, the shape the web app's graph view is built against.
- Every module could be a plugin: `graph` reads contracts and logs through kernel types, never through `workflow` or
  `telemetry` (the import-linter independence contract).
- No central server ([ADR 0027](0027-nothing-is-sent-home.md)): the team repository carries the logs, as it carries
  contracts.

## Considered Options

- **Promises from team contracts' schedules, states from entries; a log file per Heinzelmännchen copied into the team
  repository by `hz report sync`; JGF v2 out; the keeper a playbook contract reading a `team-graph` capability** (chosen)
- **Wait for ADR 0049's spine and `hz/state`** (action entries as spine events): the right end state, but nothing to
  observe for weeks; the entry shape and the graph are what carry over, the storage is what changes.
- **Push and commit automatically on every report:** writes to a remote on each action, needs a lease or retries
  when two members push at once, and turns a metadata log into a stream of commits; an explicit `sync` comes first and
  the automatic step next.
- **An explicit due time in every contract (`hz.due`):** a new contract key, while the reader's schedule already says by
  when it needs its inputs; kept for when a leaf contract (nobody reads it) needs a deadline other than its next run.
- **The keeper as code in `graph`** (a job that computes nudges): the keeper reasons about what is blocked and how to
  say it, which is a contract's job; code would also make it the one Heinzelmännchen that isn't one.

## Decision Outcome

Chosen option: **promises from team contracts, states from telemetry, one log per Heinzelmännchen synced into the team
repository, JGF v2 out, and the keeper a playbook**, because it observes the team today with what exists (contracts, the
team repository, the web head) and leaves storage to ADR 0049 without changing the shape anyone builds on.

1. **A promise.** Each run of a scheduled, valid team contract (a slot of its `schedule` in `[graph] time_zone`, else
   the zone the daemon uses, with the daemon's daylight saving rules) promises its artifact, `contract:team/<name>#<output>`, by a **deadline**: the next run of a team
   contract that reads it, or else its own next run. The kernel's `TeamPromises` (provided by `workflow`) hands `graph`
   each contract's promise: contract, title, version, schedule, the artifact it writes, the team artifacts it reads and
   its publish operations, never its body. Unscheduled contracts promise nothing.
2. **Its state**, one of five, from the entries its keeper reported between that slot and the next: kept (published, or
   approved when the contract publishes nothing) is `on_time` by the deadline, `late` after it, `fell_back` when it also
   used a stale input; not kept is `late` once the deadline passed, and before it `offline` when the keeper was silent
   for `[graph] offline_after` (2 days), `fell_back` when it reported a stale input, `waiting_for_approval` when it
   drafted and nobody approved, else `on_time`. An entry counts for the run whose slot it follows, so an update made
   before its slot counts for the run before. Runs before a keeper's first entry aren't its promises. A
   Heinzelmännchen has one user and keeps one team contract: entries of another user under its id, or for another
   contract, are skipped and named in `problems`. An artifact takes the
   worst state of its keepers; a team contract nobody's Heinzelmännchen reports for gets a keeper named after it, which
   shows as `offline`.
3. **Telemetry entries** (`heinzel.TelemetryEntry`, `docs/schemas/telemetry-entry-1.json`). Fixed fields: `heinzel_id`
   (a slug; it becomes ADR 0049's durable id), `user` (the roster handle), `time` (stored in UTC), `verb`, `target`,
   `description` (one line, at most 280 characters), and `contract` (`team:<name>`, never a personal one) and `part`
   (a part of the target, such as a tracker row, which becomes a node whose `parent` is the artifact). Versions are
   plain tokens, checked for credentials like descriptions. The verbs are
   ADR 0052's: `read` (`version`, `needed_by`), `drafted` (`contract_version`), `approved` (`by`; the entry's time is
   when), `published` (`version`), `fell_back` (`input`), `heartbeat` (no target). A target is an https URL without
   user, password, credential-like query or fragment parameters, never a webhook (whose URL is a credential), an email
   address (only for `published`), `contract:team/<name>#<output>` or `chat:<channel>`; plain http and local paths
   are refused (a draft on your disk is yours). Descriptions with an email address,
   control characters or anything that looks like a token are refused; unknown fields are refused. Email addresses
   are allowed only as publish targets: the team needs to know a report went to its list, never what it said.
4. **The logs.** The package `telemetry` is the module `report`: `hz report <verb> <heinzel> <target> ...` (one command per verb; CLI and MCP, never REST or the web
   head, since each writes) appends to `<state dir>/telemetry/<heinzel_id>.jsonl` under an exclusive lock, refusing a
   symbolic link, a log of another user (one writer per log) or for a second contract, a time in the future or older
   than 30 days, and a log over 8 MiB. `[report] user` names you, and no option overrides it (a project `hz.toml`
   can't set it), so nothing you run files entries under a teammate's handle. `hz report sync` copies each log to
   `telemetry/<user>/<heinzel_id>.jsonl` in the team repository's checkout (`OrgContext`) only when the copy there is a
   prefix of it, so a team file has one writer, through a fresh temporary file it alone named, never into a link, a
   FIFO or anything but a regular file, and says what to commit and push. Readers validate every line again
   and skip, by file and line, a line filed under another user or Heinzelmännchen than its own.
5. **`hz graph`** (module `graph`, one operation: `hz graph`, the MCP tool, and `GET /api/graph` on the web head;
   not on the unauthenticated REST head, since it reads team data) builds the graph afresh from `TeamPromises` and the
   kernel's `TelemetryLog` (provided by `telemetry`: the team's copies plus your own logs, yours winning). Filters:
   `--artifact` (a node id, its parts and what touches them), `--heinzel` (both together select both), `--state`
   (narrows that selection); `--at` shows it as of a time. A node's `url` is an https place only, never an address.
   `--json` is a JSON Graph Format v2 document, profile `hz-team`, generated as `docs/schemas/team-graph-1.json`:

   ```json
   {"graph": {"directed": true, "type": "hz-team", "label": "Team graph",
     "metadata": {"as_of", "time_zone", "filters": {"artifact", "heinzel", "state"},
                  "weeks": [{"week": "2026-W43", "due", "on_time", "fell_back", "late"}],
                  "waiting": [{"artifact", "summary": "Weekly report waiting on 2 updates", "on": [ids]}],
                  "summary", "problems"},
     "nodes": {"<id>": {"label", "metadata": {"kind": "artifact" | "heinzel", "owner", "state", "contract",
                                             "contract_version", "deadline", "url", "parent", "last_seen"}}},
     "edges": [{"source": "heinzel:<id>", "target": "<artifact id>", "relation": "reads" | "writes",
                "metadata": {"state", "declared", "observed", "last_version", "needed_by"}}]}}
   ```

   Heinzelmännchen are `heinzel:<id>`; artifacts are their contract output, or the target telemetry named. `declared`
   edges come from contracts (the keeper of a contract writes its artifact and reads its inputs), `observed` ones from
   entries; a write with a `part` goes to the part. Descriptions never reach the graph. `weeks` count promises whose
   deadline fell in the week and are decided; `waiting` lists, for each scheduled contract that reads team artifacts,
   the updates due by its next run and not made.
6. **The graph keeper is a Heinzelmännchen.** `graph` serves the capability `team-graph` (method `snapshot`, read
   only) with its adapter `hz-graph`, the default binding, and declares a playbook (no setup step) whose template
   `graph:team-graph-keeper` is a contract that reads `team-graph.snapshot` and drafts a note for its own user on where
   the flow waits and what changed; it publishes nothing and addresses nobody else. The packet builder drops the graph's
   email addresses like any input's. This amends [ADR 0037](0037-plugins-bring-playbooks.md)'s "the core knows no
   playbook": the keeper is about hz's own graph, not a kind of team work.
7. **Next steps.** Automatic sync (each daemon tick commits and pushes the user's logs, or ADR 0049's `hz/state` branch
   carries them as spine events); hz writing the entries for what it does itself (ADR 0052 §2), so `report` remains for
   what it can't see; `hz.due` for leaf contracts; ADR 0052's aliases and conflicts.

### Consequences

- Good: a manager sees "weekly report waiting on 2 updates" and the kept-on-time weeks without asking anyone, and the
  web app draws the same JSON the tests hold.
- Good: entries are small, strict and the same shape ADR 0049's spine will store; moving the storage changes neither
  `hz graph` nor the web view.
- Bad: until hz writes entries itself, a promise is only as observed as its Heinzelmännchen's reports; an unreported
  approval shows as waiting. Until sync is automatic, the team sees your entries when you push them.
- Bad: a leaf contract is due by its own next run, which is lenient; `hz.due` would tighten it.
- Bad: four kernel types (`TelemetryEntry`, `TelemetryLog`, `TeamPromises`, the `TeamGraph` protocol) join the public
  API (ADR 0026).

### Confirmation

`tests/unit/kernel/telemetry_test.py` (what an entry accepts and refuses); `tests/unit/modules/telemetry_service_test.py`
(appending under a lock, one writer per log, dates, links, sync's prefix rule, re-validating the team's copies);
`tests/unit/modules/graph_build_test.py` (every state, deadlines from readers and from the own next run, weeks, the
waiting list, observed and declared edges, cuts, zones); `tests/unit/modules/workflow_promises_test.py` (only valid
team contracts promise); `tests/integration/graph_test.py` (`hz graph --json` equals
`tests/fixtures/graph/weekly-report.jgf.json`, which equals `web/src/test/fixtures/graph.jgf.json` and validates against
the schema; filters; report, sync and the graph through the CLI; not on REST, on the web head; the keeper template
checks, binds `hz-graph` and dry-runs on the graph); `make docs` keeps both schemas in step with the models; the
import-linter independence contract lists `graph` and `telemetry`.

## More Information

- **Naming beside ADR 0052.** The verbs are 0052's action names. The fixed fields keep the maintainer's names for this
  step (`heinzel_id`, `time`; 0052 writes `heinzel`, `ts`), and `drafted`'s and `published`'s place is the `target`
  (0052's `where`); the command is `hz report <verb>` rather than 0052's `hz heinzel report`, since the `heinzel` module
  of [ADR 0051](0051-the-workflow-module-becomes-the-heinzel-module.md) isn't built. When it is, the spine adopts this
  shape and `hz heinzel report` becomes the command, with `hz report` kept as its alias for a release.
- 0052's open question, what team members see of each other, is answered here only as far as the maintainer decided:
  states, deadlines and edges per Heinzelmännchen and artifact, never descriptions, costs or per-person counts.
