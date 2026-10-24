---
status: proposed
date: 2026-10-23
amends: 0010
related: 0020
---

# 0021: Workflows are Markdown contracts with YAML frontmatter

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
readable prose. Each point says what 0.2 builds and what comes later.

1. **File and parser.** A workflow is `<name>.prose.md`: YAML frontmatter between `---` lines, then a Markdown body;
   `name` equals the file stem (`HZC010`).
   - 0.2: PyYAML reads it, with a loader that keeps only YAML 1.2 core scalars, so `on`, `yes`, dates and `1:20` stay
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
   - 0.2: `schema: 1`, `title`, `scope` (`personal`), `schedule` (five cron fields or a shortcut such as `@daily`;
     none means by hand only), `requires`, `inputs`, `outputs` (exactly one), `publishes`, `agent` (`tools`,
     `max_usd`), `timeout` (the agent session, ≤2h, default 10m), `retries` (0 to 3) and `catch_up` (`latest` or
     `skip`).
   - Later, refused until then with `HZC023` (all such keys reported at once): `owner`, `maintainers`, `tags`,
     `freshness`, `on`, `tools`, `concurrency`, `cost`, `idempotency`, `overrides`, `scope: team`, `catch_up: all`,
     output `target` and facets, input `source`/`query`/`facets`/`stage`/`required`, publish `auto_approve` and
     `target`. Agent `runner`, `model`, `effort` and `max_turns` are refused for good: the runner and model are
     configuration ([ADR 0029](0029-agent-runners-are-capability-adapters.md)).
3. **Requirements and inputs name capabilities, never vendors** ([ADR 0028](0028-integrations-are-capability-adapters.md)).
   - 0.2: `requires` lists `<capability>:read`; `agent-runner:read` is implied; a contract never pins an adapter and
     never writes. An input is `{read: <capability>.<method>, args, window}`, checked against the protocol method's
     signature; its window ends when the run starts.
   - Later: windows end at the schedule slot, with memo keys (8).
4. **Body.** Required: `### Goal`, `### Maintains` and `### Done when` (with items); `### Requires` when there are
   inputs. Optional: `### Continuity`, `### Steps`, `### Invariants`, `### Description`. Retired OpenProse sections and
   `### Execution` are rejected (`HZC106`); unknown sections are warnings (`HZC105`). HTML comments are authors' notes,
   never shown to the agent.
   - 0.2: backticked items must match the keys of `hz.inputs` and `hz.outputs` (`HZC103`); `### Tools` is "not
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
   - 0.2: one bounded agent session per run, inputs pre-fetched, no hz tools.
   - Later: inputs compiled into shared `WatchLet` instances; `JobLet` gains `fingerprint`, `freshness` and `cost`.
7. **The graph** (later). Edges come only from explicit `contract:<namespace>/<name>#<facet>` inputs, never semantic
   matching; downstream contracts see published truth unless they declare `stage: draft`; cycles are reported and
   disable the contracts on them; team contracts can't depend on personal ones.
8. **Fingerprints** (later). All are SHA-256 over RFC 8785 canonical JSON of normalized text. `contract_fingerprint`
   covers intent only (kind, inputs, outputs, publish actions and targets, tools, one hash per behavior-bearing
   section), computed from the migrated model with defaults filled in; it excludes identity, display, scheduling,
   agent hints, limits, policy references, `### Continuity` and comments. `source_fingerprint` covers the exact
   canonical text and pins embedded workflows. The memo key is `(contract_fingerprint, input facet fingerprints,
   freshness_epoch)`.
   - 0.2: each input's data is hashed in the render's record; the whole file's SHA-256 pins team contracts (10).
9. **Approvals and cost.** Every `publishes` entry names a publish operation (`read_only=False`, `HZC013` otherwise),
   which declares `write` itself; the contract never does.
   - 0.2: nothing is published; drafts are saved locally and wait for `hz inbox`. Every run is capped by
     `[workflow] max_usd` (user scope, default 1.0 USD) and the contract's own `agent.max_usd`, whichever is lower; a
     scheduled slot splits that cap evenly across its attempts (`1 + retries`), so the slot never spends more.
   - Later: approvals in the state repository; `auto_approve` names a policy but grants nothing, and is ignored after an
     intent change and for corrections.
10. **Locations and trust.** Qualified names are `<namespace>:<name>`; a bare name that exists in two namespaces is an
    error that asks for the prefix, in every head.
    - 0.2: `me` is `[workflow] user_dir` (default `<config dir>/workflows`), `team` is `workflows/` in a checkout of
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
    - 0.2: `hz workflow list`, `show <name>` (schedule, daemon job or why there is none, requirements with their
      adapters, inputs, outputs, publishes, limits, the SHA-256 to approve, diagnostics), `check [--path]` (exits with 1
      on errors, for a team's CI; a path must be inside your workflows directory, the team repository or the current
      directory, because every head exposes `check`) and `run <name> [--dry-run]` (CLI and MCP only: a render spends
      money, and a web page can reach a local REST server, [ADR 0019](0019-mcp-and-rest-heads.md)). What `check` accepts
      compiles: the schedule is checked with the kernel's own cron shape check, and a contract the kernel would refuse
      as a job is invalid in `check` too; compiling one contract never stops the others. A manual run saves the draft and a
      `.record.json` but no ledger receipt and says so; receipted runs go through the daemon
      (`hz system daemon tick --job workflow:<namespace>-<name>`).
    - Later: `new`, `graph`, `fingerprint`, `migrate`, `export --prose`.

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
  diagnostics and a health check (0.2); the compiler to `WatchLet`, graph checks and the fingerprint functions with
  golden tests (later).
- Neutral: Material and Immaterial notes are honored by the render in v1. A deterministic canonicalizer from those
  notes is follow-up work.

### Confirmation

Partly, for the points marked 0.2 (see Status under More Information): the strict YAML loader and its limits (an
alias bomb and 5,000-deep nesting finish fast), the frontmatter schema, the body checks and the `HZC` diagnostics with
their lines (`tests/unit/modules/workflow_contracts_test.py`); discovery, one name in two namespaces, publish and tool
checks, compilation into daemon jobs, the cost cap and its split, pinned team contracts, the packet with inputs
fetched through capability handles and its size caps, drafts and their records, and the health check, with test-only
adapters and a fake agent runner (`tests/unit/modules/workflow_service_test.py`); the committed JSON Schema against
the example and the filled model (`tests/unit/modules/workflow_schema_test.py`, and `make check` for drift);
`hz workflow` through the CLI and REST heads, a receipted daemon run, one bad schedule leaving the other jobs and
matrix rows intact while `check` fails, compiled jobs in the capability matrix, and a hostile project `hz.toml` that
can't point discovery at its own contracts or raise the cap (`tests/integration/workflow_test.py`); contract names,
titles, error messages and doctor text shown as written, never as Rich markup and never with control characters
(`tests/integration/cli_plain_text_test.py`, `tests/unit/heads/cli_render_test.py`); `run` not reachable over REST
(`tests/integration/rest_security_test.py`); the daemon running compiled jobs and surviving a failing compiler
(`tests/unit/modules/daemon_service_test.py`); the kernel's `handle_for` serving only the compiler's read-only jobs
(`tests/unit/kernel/bindings_test.py`). No test runs a real agent. Still planned: golden tests for fingerprints,
the contract graph, and `hz workflow check` in a team repository's CI template.

## More Information

- Amends [ADR 0010](0010-towards-a-reactive-workflow-engine.md) (contract format) and replaces the `ContractLet` of
  [ADR 0020](0020-daemon-head.md) with `CompiledJobs`.
- Research, schema and examples: [docs/research/workflow-contracts.md](../research/workflow-contracts.md); the example
  contract the tests use: `tests/fixtures/workflows/weekly-digest.prose.md`.
- **Status.** Proposed. 0.2 builds the points marked 0.2 above in the `workflow` module (director's decisions,
  2026-10-24, and on review of the pull request, 2026-10-24); fingerprints, memo keys, the graph, watches, team scope,
  approvals, migration and embedding are not built.
- **Interim lineage.** A manual run's `.record.json` (the packet, each input's hash and the agent's report) is interim
  lineage, not a receipt: it isn't hash-chained, and nothing lists or verifies it. Daemon runs get the chained receipt
  with the same content. When the `Ledger` protocol moves into the kernel (0.3), manual runs write receipts too and the
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
