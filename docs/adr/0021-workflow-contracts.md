---
status: proposed
date: 2026-10-23
amends: 0010
related: 0020
amended-by: 0049, 0051, 0052
---

# 0021: Workflows are Markdown contracts with YAML frontmatter

*Since 0051 the module is `heinzel`: read `hz workflow` as `hz heinzel` and `[workflow]` as `[heinzel]`.*

## Context and Problem Statement

Teams add workflows by writing files, not code (ADR 0010). OpenProse's contract Markdown gives us the vocabulary we
want: `### Goal`, `### Requires`, `### Maintains` with `####` facets, `### Continuity`, `### Invariants`,
postconditions instead of a judge. But OpenProse keeps frontmatter to identity only (`name`, `kind`, `id`,
`version`), forbids tools in frontmatter, and wires contracts through a compile step that runs model sessions
(Forme). ADR 0010 promised "a subset of OpenProse contract Markdown"; taken literally, that leaves the kernel nothing
typed to read. What is the file format of a workflow?

## Decision Drivers

- The daemon head boots the kernel on every tick, and without calling a model it must decide which jobs are due,
  compute memo keys, validate approvals, and list contracts through CLI, MCP and REST (ADR 0020).
- Comparable formats split the same way: GitHub Actions, Kestra, dbt and CWL put triggers, inputs, limits and
  references in typed YAML. Agent Skills, Claude Code skills, Prompty and static-site generators put a small YAML
  block over a Markdown body, and keep tool-specific keys in a single map. Dagster and Prefect show that caching
  works only when the definition states exactly what is hashed.
- Embedded workflows (a "🧝 Heinzel" tab) need a text form that survives rich editors and can be pinned by
  fingerprint.

## Considered Options

- **OpenProse-compatible Markdown body with YAML frontmatter, hz keys nested under `hz:`** (chosen)
- **Pure OpenProse (identity-only frontmatter, everything in sections, Forme wiring):** needs a model to compile
  and wire, which puts a model on the scheduling and safety path, and gives the kernel nothing typed to expose.
- **Hz keys at the top level of the frontmatter:** flatter to write, but mixes namespaces, makes OpenProse tooling
  meet many unknown keys, and blurs which keys are ours.
- **Pure YAML workflows (GitHub Actions, Kestra, CWL style):** typed and familiar, but the goal, materiality and
  postconditions become string blobs. They are poor to read and review, and break with Reactor's "Markdown is
  intent".
- **Python-only `JobLet`s:** they remain available for code-heavy jobs, but they exclude non-developers (tenet 2)
  and cannot be embedded in documents.
- **TOML frontmatter (`+++`):** consistent with `hz.toml`, but unfamiliar in Markdown tools, unsupported by
  OpenProse and skill tooling, and awkward for nested lists.
- **JSON workflow graphs (n8n style):** diff and review badly; no place for prose intent.
- **Semantic matching of Requires to Maintains:** convenient, but not deterministic. Kept only as a suggestion
  tool (`hz workflow graph --suggest`) that proposes explicit edges by pull request.
- **Hashing the whole file for the memo key:** simple, but a typo fix, a new tag or a cron change would re-render
  everything and wake every downstream contract. The whole-file hash is kept only as `source_fingerprint` for
  pinning.
- **Reactor as a runtime dependency:** alpha and TypeScript (ADR 0010, ADR 0020). We keep the body dialect
  compatible instead.

## Decision Outcome

Chosen option (proposed): **a workflow is a `<name>.prose.md` file with YAML frontmatter, hz keys under `hz:`, and
an OpenProse-compatible Markdown body**, because the kernel gets typed data without a model while the intent stays
readable prose. Each point says what the first release builds and what comes later.

1. **File and parser.** A workflow is `<name>.prose.md`: YAML frontmatter between `---` lines, then a Markdown body;
   `name` equals the file stem (`HZC010`).
   - First release: PyYAML reads it, with a loader that keeps only YAML 1.2 core scalars, so `on`, `yes`, dates and `1:20` stay
     strings, and numbers with a leading zero stay strings too. Duplicate and non-string keys, anchors and aliases,
     explicit tags other than the core scalar ones, nesting deeper than 32, more than 10,000 values and files over
     256 KiB are refused before anything is built, and so is a file holding control characters other than tab and line
     feed (`HZC005`): escape sequences in a title or a diagnostic would reach whoever reads it on a terminal. Any
     failure is that file's diagnostic and never stops the others or the daemon.
   - Later: a rewriting operation (`migrate`, `validate --fix`) needs a writer that keeps comments and layout, such as
     ruamel.yaml's round trip; reading doesn't.
2. **Frontmatter.** The top level is OpenProse-compatible: `name` (lowercase words joined by single dashes, ≤64; a
   subset of the kernel's job names), `kind: responsibility`, `description` (≤1024), optional `id` and `version`;
   `x-*` keys are ignored. Every hz key is under `hz:`; keys that exist on `JobLet` keep its names.
   - First release: `schema: 1`, `title`, `scope` (`personal`), `schedule` (five cron fields or a shortcut such as `@daily`;
     none means by hand only), `requires`, `inputs`, `outputs` (exactly one), `publishes`, `agent` (`tools`,
     `max_usd`), `timeout` (the agent session, ≤2h, default 10m), `retries` (0 to 3) and `catch_up` (`latest` or
     `skip`).
   - Later, refused until then with `HZC023` (all such keys reported at once): `owner`, `maintainers`, `tags`,
     `freshness`, `on`, `tools`, `concurrency`, `cost`, `idempotency`, `overrides`, `scope: team`, `catch_up: all`,
     output `target` and facets, input `source`/`query`/`facets`/`required`, publish `auto_approve` and
     `target`. Agent `runner`, `model`, `effort` and `max_turns` are refused for good: the runner and model are
     configuration ([ADR 0029](0029-agent-runners-are-capability-adapters.md)).
   - [ADR 0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md)
     statements 13, 15 and 20, borrowed from OpenProse Reactor
     ([harness spec](https://github.com/openprose/reactor/blob/main/spec/02-ReactorHarness.md),
     [pattern](https://github.com/openprose/reactor/blob/main/spec/03-ReactorPattern.md)), define four keys.
     They stay refused with `HZC023` until ADR 0049's staged step 6 builds them; `freshness` stays deferred.
     - Input `facet` (singular; it replaces the deferred input `facets`, and two facets are two inputs): one facet of
       what a capability input reads, a document's tab or heading, a tracker's row; the input then wakes only when that
       facet's fingerprint moved (8). A `contract:` input keeps its `#<output>` suffix (7) and takes no `facet`.
     - Input `recheck` (`5m` to `7d`, default every tick): how often the daemon may look, so a volatile input is
       polled often and a roster weekly. It decides when to look and never wakes anyone by itself.
     - Output `valid_for` (`1h` to `366d`): the approved text expires that long after approval, and the
       Heinzelmännchen then wakes with cause `stale`.
     - `hz.render_on_schedule: if-moved | always`: whether a schedule slot renders when no input moved. `if-moved`
       gathers, fingerprints and ends `skipped` with reason `nothing-moved` at no model cost; `always` renders every
       slot, for an output that must exist each time ("the Friday section, even if nothing merged"). An absent key
       means `always`, so no existing contract changes behaviour; `hz heinzel new` writes `if-moved` into the personal
       contracts it creates, and `if-moved` is refused for team contracts until ADR 0054 records how a skip counts
       towards a promise.
3. **Requirements and inputs name capabilities, never vendors** ([ADR 0028](0028-integrations-are-capability-adapters.md)).
   - First release: `requires` lists `<capability>:read`; `agent-runner:read` is implied; a contract never pins an adapter and
     never writes. An input is `{read: <capability>.<method>, args, window}`, checked against the protocol method's
     signature. Its window looks back from the run (`7d`, `last 7d`: it ends when the run starts) or ahead of it
     (`next 7d`: it begins then, e.g. a calendar's coming week), at most 366 days either way: a forward window is just an input window. Its data is hashed like any other input's.
   - Later: windows end, or begin, at the schedule slot, with memo keys (8).
4. **Body.** Required: `### Goal`, `### Maintains` and `### Done when` (with items); `### Requires` when there are
   inputs. Optional: `### Continuity`, `### Steps`, `### Invariants`, `### Description`. Retired OpenProse sections and
   `### Execution` are rejected (`HZC106`); unknown sections are warnings (`HZC105`). HTML comments are authors' notes,
   never shown to the agent.
   - First release: backticked items must match the keys of `hz.inputs` and `hz.outputs` (`HZC103`); `### Tools` is "not
     yet" (`HZC023`) while `hz.tools` is deferred.
   - Later: `####` facets with Material and Immaterial notes checked against output facets.
5. **Models and schema.** Pydantic models with described fields are the source of truth; the JSON Schema is
   generated as `docs/schemas/contract-frontmatter-<n>.json`, `make check` fails on drift, and a test validates the
   example against it. Diagnostics carry file, line, key, a stable `HZC` code and a hint, also as JSON.
   - Later: migration functions for `hz.schema` 2 and `hz workflow migrate`.
6. **Compilation.** Contracts are data, so there is no `ContractLet`. The `workflow` module is the single compiler
   of every contract source (files now; plugin-shipped and embedded contracts later are discovered by it too). It
   provides the kernel's `CompiledJobs` service; the kernel calls its factory afresh whenever jobs are listed, and the
   daemon runs them beside the declared `JobLet`s as `workflow:<namespace>-<name>`, so `me:x` and `team:x` are two
   jobs. Each job acts as a `Requester` of kind `job` of that module, built from the contract, and gets handles
   through `Kernel.handle_for`, which serves only the compiling module's jobs, read only and without adapter pins;
   compiled jobs appear in the capability matrix. A failing compiler is reported and the declared jobs still run.
   - First release: one bounded agent session per run, inputs pre-fetched, no hz tools.
   - Later: inputs compiled into shared `WatchLet` instances; `JobLet` gains `fingerprint`, `freshness` and `cost`.
7. **The graph.** Edges come only from explicit `contract:<namespace>/<name>#<facet>` inputs, never semantic
   matching; downstream contracts see published truth unless they declare `stage: draft`; cycles are reported and
   disable the contracts on them; team contracts can't depend on personal ones.
   - First release: an input `{read: "contract:<namespace>/<name>#<output>", stage: draft}` reads the
     newest local draft of that contract's output (its text, or its JSON), hashed like any input; it takes no `args`
     or `window` and needs no capability. `stage: draft` is required, since nothing is published yet. `hz workflow
     graph` (text, `--json`, `--mermaid`) derives the graph from the contracts found: contracts, the assets they
     maintain, the sources they read, and edges between them. A contract on a cycle, its own output included, is an
     error (`HZC030`) and doesn't run; an input no contract maintains is a warning (`HZC031`) and fails that run; a
     team contract reading a personal one's output is an error (`HZC032`). These are diagnostics of the reading
     contract, found only when the whole set is read, not by `check --path`.
   - Later: published truth as the default stage, output facets in place of output names, and `--suggest`.
8. **Fingerprints** (later). All are SHA-256 over RFC 8785 canonical JSON of normalized text. `contract_fingerprint`
   covers intent only (kind, inputs, outputs, publish actions and targets, tools, one hash per behavior-bearing
   section), computed from the migrated model with defaults filled in; it excludes identity, display, scheduling,
   agent hints, limits, policy references, `### Continuity` and comments. `source_fingerprint` covers the exact
   canonical text and pins embedded workflows. The memo key is `(contract_fingerprint, input facet fingerprints,
   freshness_epoch)`.
   - Per [ADR 0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md): an
     input's fingerprint is a map `{facet: hash}`, computed by hz over the canonical content per facet the adapter
     returns (ADR [0028](0028-integrations-are-capability-adapters.md)); an adapter that knows no facets returns one
     facet, `@all`, the whole result. An input with `facet` (2) compares only that facet, so a change in another tab of
     the same document wakes nobody who reads this one. The freshness epoch advances only when an approved output's
     `valid_until` (approval plus `valid_for`, 2) passes, and the wake it causes has cause `stale`; a `recheck` interval
     moves nothing in the key. As in Reactor, nothing in the memo key is judged by a model: facets and what is material
     are decided by adapter code, never by a model compiling the contract.
   - First release: each input's data is hashed in the render's record; the whole file's SHA-256 pins team contracts (10).
9. **Approvals and cost.** Every `publishes` entry names a publish operation (`read_only=False`, `HZC013` otherwise),
   which declares `write` itself; the contract never does.
   - First release: nothing is published; drafts are saved locally and wait for `hz inbox` (not built). Every run is capped by
     `[workflow] max_usd` (user scope, default 1.0 USD) and the contract's own `agent.max_usd`, whichever is lower; a
     scheduled slot splits that cap evenly across its attempts (`1 + retries`), so the slot never spends more.
   - Later: approvals in the state repository; `auto_approve` names a policy but grants nothing, and is ignored after an
     intent change and for corrections.
10. **Locations and trust.** Qualified names are `<namespace>:<name>`; a bare name that exists in two namespaces is an
    error that asks for the prefix, in every head.
    - First release: `me` is `[workflow] user_dir` (default `<config dir>/workflows`), `team` is `workflows/` in a checkout of
      the team's state repository (`[workflow] team_repository`). A project `hz.toml` can't set `user_dir`,
      `team_repository`, `schedule_team` or `max_usd`, and doctor names the keys it tried: a repository someone else
      controls must not decide which prompts your daemon runs or what they cost. A contract is data: it gets read
      handles for what it declared through the adapters configuration binds. Symbolic links are skipped. Team contracts
      run by hand, and on your daemon only when `[workflow] schedule_team` names them, optionally pinned as
      `name@sha256:<hex>`; a pinned contract whose file changed doesn't run, and doctor says so.
    - Later: project contracts in `.hz/workflows/` (`project`, personal scope only), module and plugin contracts
      (namespace = module), embedded registered contracts (`doc`), `hz.overrides`, and team scope with leases.
11. **Embedding** (later). The canonical text is the file text. A leading fenced `yaml` block is accepted in place of
    `---`, and headings are matched relative to the shallowest level. Both forms are converted to the canonical form
    before fingerprinting; this must be one function with ADR 0022's `canonical-v1`.
12. **Operations.**
    - First release: `hz workflow list`, `show <name>` (schedule, daemon job or why there is none, requirements with their
      adapters, inputs, outputs, publishes, limits, the SHA-256 to approve, diagnostics), `check [--path]` (exits with 1
      on errors, for a team's CI; a path must be inside your workflows directory, the team repository or the current
      directory, because every head exposes `check`) and `run <name> [--dry-run]` (CLI and MCP only: a render spends
      money, and a web page can reach a local REST server, [ADR 0019](0019-mcp-and-rest-heads.md)). What `check` accepts
      compiles: the schedule is checked with the kernel's own cron shape check, and a contract the kernel would refuse
      as a job is invalid in `check` too; compiling one contract never stops the others. A manual run saves the draft and a
      `.record.json` but no ledger receipt and says so; receipted runs go through the daemon
      (`hz system daemon tick --job workflow:<namespace>-<name>`).
    - First release, the success measure of a drafting contract (the validation slice): each render
      records its drafting time; `sent <name> --file <f>` or `--ref <document>` (the approved version where the asset
      lives, read through `documents`) records the share of words changed, both word counts and a hash, never the
      text, next to the draft; `show` reports both per draft. It is about drafts, never about people.
    - First release, `graph` (text, `--json`, `--mermaid`; every head, read-only, like `check`: it lists contracts and what they
      read, never fetched data) draws the graph of (7). `new` writes a starter contract.
    - Later: `fingerprint`, `migrate`, `export --prose`.
13. **Retention.** Drafts and their
    `.record.json` files, which hold the packet with the fetched inputs, expire `[workflow] retention_days` after their
    render (default 90; 0 keeps them forever; 0 to 36,500; a project `hz.toml` can't set it). Receipts don't expire.
    - **Deleting is housekeeping, never a read.** `hz workflow prune [--dry-run]` (CLI and MCP: it deletes your files and
      a dry run names them) and the daily job `workflow:prune` delete them; it is an ordinary `JobLet`, so the daemon's
      tick runs it with a receipt that counts what went and names no file (ADR 0020, decision 10). `drafts`, `show`,
      `list` and the health check never delete; `hz system doctor` says how much `prune` would delete, and degrades
      once expired files are more than two days overdue (nothing is pruning them; a daily slot a laptop slept through
      still runs at the next tick).
    - **Only what hz wrote, only where it wrote it.** Files named as a render names them (`<stamp>[-n].md`, `.json`,
      `.record.json`), in a contract's directory below `<data dir>/workflow/drafts/`; their age comes from the stamp in
      the name, not the file's times. The walk opens each directory relative to the last with `O_NOFOLLOW` and unlinks
      relative to its descriptor, so a symbolic link, also one swapped in while it runs, can't make it delete elsewhere;
      `workflow` or `drafts` being a link is an error. Directories stay (a render may be writing into one); a file
      already gone is not an error, so pruning twice deletes nothing more. A contract directory or file that can't be
      read or deleted is reported and the rest are still pruned; `prune` then exits with 1 and the job fails.
    - **Receipts are kept, and hold no draft text** (chosen over pruning receipt payloads or checkpointing the chain).
      A receipt's id hashes its content and chains it to the job's previous receipt, so deleting a receipt or its
      payload would break every later link, and a checkpoint (a signed or recorded summary of the chain so far) is
      machinery the shared ledger (0.3) should own. Instead, a compiled job's receipt carries the render without the
      agent's draft text: the draft's path, its SHA-256, the record's path, the input hashes, the agent's status,
      model, usage and cost. That is lineage without content, small enough to keep, and the chain stays verifiable
      because nothing is removed. A receipt's draft path can outlive the draft; its hash still identifies it.
    - **First-release receipts** of compiled jobs hold the draft's text in `result.agent.draft`. They aren't rewritten, which
      would break their chain; moving a job's receipts aside, the documented way to start a chain again, drops them.

14. **What a contract is, and where the workflow is.** A contract is an
    invariant about one asset ("keep X true given Y"); the overall workflow is not written anywhere but emerges from
    the graph of contracts linked by inputs/outputs. Events are just "an input changed" (fingerprints), besides clock
    and manual triggers.
    - A contract states one invariant: it keeps one asset, its output, true given its inputs. `### Goal` and
      `### Maintains` say what is true; `### Done when` is how the agent checks it.
    - Until publishing exists, the output is the asset's draft: the graph's asset nodes are a contract's outputs
      (`contract:<namespace>/<name>#<output>`). Where the asset itself lives (ADR 0037's asset types and `kept_by`, a
      document a playbook names) is not a node yet; when a contract can name it, the output is drawn as the draft of
      that asset, and the node keeps its id.
    - There are no workflow files and no pipeline definitions. The workflow is the graph (7): contracts linked by the
      outputs they maintain and the inputs that read them. Adding a step is adding a contract that reads another's
      output; `hz workflow graph` shows the result.
    - A contract wakes for three reasons only: the clock (`schedule`), a person (`hz workflow run`, `tick --job`) and,
      later, an input that changed, detected by its fingerprint (8) rather than by a separate event system. The first release builds
      the first two; input wakes come with memo keys and `WatchLet`s.

### Consequences

- Good: a team adds a workflow with one reviewable Markdown file. The same text works as a file, in a plugin, or
  embedded in a document.
- Good: the daemon decides whether to render with pure Python. Changing a cron expression, a model hint or a cost
  ceiling does not re-render anything; changing intent does, and re-arms human review.
- Good: explicit edges cost authors a line per dependency, but give a graph that can be reviewed and is
  deterministic.
- Neutral: the body stays readable as an OpenProse responsibility. The frontmatter is an extension under one key,
  so ADR 0010's "subset of OpenProse" now holds for the body only. An exporter folds `### Done when` back into
  Maintains postconditions for OpenProse tooling.
- Bad: body and frontmatter can disagree, so validation must check both. `hz workflow check`, and later a fixer
  and agent-drafted pull requests, keep the cost low.
- Bad: new work: the parser, the models and the generated schema, the compiler to `CompiledJobs`, the `HZC`
  diagnostics and a health check (first release); the compiler to `WatchLet`, graph checks and the fingerprint functions with
  golden tests (later).
- Neutral: Material and Immaterial notes are honored by the render in v1. A deterministic canonicalizer from those
  notes is follow-up work.

### Confirmation

Partly, for the points marked "First release" (see Status under More Information): the strict YAML loader and its limits (an
alias bomb and 5,000-deep nesting finish fast), the frontmatter schema, the body checks and the `HZC` diagnostics with
their lines (`tests/unit/modules/heinzel_contracts_test.py`); discovery, one name in two namespaces, publish and tool
checks, compilation into daemon jobs, the cost cap and its split, pinned team contracts, the packet with inputs
fetched through capability handles and its size caps, drafts and their records, and the health check, with test-only
adapters and a fake agent runner (`tests/unit/modules/heinzel_service_test.py`); the committed JSON Schema against
the example and the filled model (`tests/unit/modules/heinzel_schema_test.py`, and `make check` for drift);
`hz workflow` through the CLI and REST heads, a receipted daemon run, one bad schedule leaving the other jobs and
matrix rows intact while `check` fails, compiled jobs in the capability matrix, and a hostile project `hz.toml` that
can't point discovery at its own contracts or raise the cap (`tests/integration/heinzel_test.py`); contract names,
titles, error messages and doctor text shown as written, never as Rich markup and never with control characters
(`tests/integration/cli_plain_text_test.py`, `tests/unit/heads/cli_render_test.py`); `run` not reachable over REST
(`tests/integration/rest_security_test.py`); the daemon running compiled jobs and surviving a failing compiler
(`tests/unit/modules/daemon_service_test.py`); the kernel's `handle_for` serving only the compiler's read-only jobs
(`tests/unit/kernel/bindings_test.py`); which files expire, links below the data directory refused and never
followed, a file that can't be deleted named, pruning twice deleting nothing more
(`tests/unit/modules/heinzel_retention_test.py`); the setting's bounds, `prune` and its dry run, retention 0, reads and
health never deleting, the doctor check and its grace day, and a compiled job's result without the draft's text
(`tests/unit/modules/heinzel_service_test.py`); `hz workflow prune` through the CLI, not over REST, the daemon's
receipted `workflow:prune`, and a project `hz.toml` that can't shorten the retention
(`tests/integration/heinzel_test.py`); windows back and ahead and their bounds, and contract inputs
(`tests/unit/modules/heinzel_contracts_test.py`); the graph, its cycles (a 300-contract chain included), unmaintained
inputs, team contracts reading personal ones and Mermaid labels that can't break out of their quotes
(`tests/unit/modules/heinzel_graph_test.py`); a contract reading the newest draft it depends on, a cycle that
doesn't run, and a calendar asked for the coming week (`tests/unit/modules/heinzel_service_test.py`); `hz workflow
graph` as text, JSON and Mermaid (`tests/integration/heinzel_test.py`). No test runs a real agent. Still planned:
golden tests for fingerprints and `hz workflow check` in a team repository's CI template.

## More Information

- Amends [ADR 0010](0010-towards-a-reactive-workflow-engine.md) (contract format) and replaces the `ContractLet` of
  [ADR 0020](0020-daemon-head.md) with `CompiledJobs`.
- Research, schema and examples: [docs/research/workflow-contracts.md](../research/workflow-contracts.md); the example
  contract the tests use: `tests/fixtures/workflows/weekly-digest.prose.md`.
- **Status.** Proposed. The first release builds the points marked "First release" above in the `workflow` module; fingerprints, memo keys, input wakes, watches, team scope,
  approvals, migration and embedding are not built.
- **Interim lineage.** A manual run's `.record.json` (the packet, each input's hash and the agent's report) is interim
  lineage, not a receipt: it isn't hash-chained, and nothing lists or verifies it. Daemon runs get the chained receipt
  with the same lineage but without the draft's text (13); the record expires with its draft. When the `Ledger` protocol moves into the kernel (0.3), manual runs write receipts too and the
  record folds into them. Until then a manual run has no receipt, and `hz workflow run` says so.
- **Why PyYAML.** It was already locked and audited (MIT) and has typed stubs; with a core-schema resolver and the
  limits above it reads frontmatter safely. ruamel.yaml stays the candidate for a comment-preserving writer.
- **Open questions.**
  - **Two canonicalization rules.** §11 here and `canonical-v1` in [ADR 0022](0022-embedded-workflows.md) must be one
    function; nothing depends on either yet.
  - **People selectors.** `owner` and `maintainers` wait for [ADR 0024](0024-people-and-org.md)'s selector grammar.
  - **Data file format for other proposals.** Settled here for contracts (YAML). Recommendation for
    [ADR 0023](0023-inventory.md) and ADR 0024: data files people write are YAML, configuration stays TOML
    ([ADR 0012](0012-layered-configuration.md)).
  - **Manual contracts in the matrix.** Only scheduled contracts are compiled jobs, so only they appear in
    `hz system capability`; `hz workflow show` reports the requirements of the others.
- **Settled disagreements** with the other proposals: the project directory is `.hz/workflows/`
  ([ADR 0016](0016-name-heinzelmaennchen.md)); the runner is configuration, not an enum
  ([ADR 0029](0029-agent-runners-are-capability-adapters.md)); inputs and publishes name capabilities and operations,
  not vendors ([ADR 0028](0028-integrations-are-capability-adapters.md)); tools: render packets pre-fetch inputs and
  the agent gets no hz tools (option a), `hz.tools` later.
