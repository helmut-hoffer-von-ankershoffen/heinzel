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
readable prose.

1. **File.** A workflow is `<name>.prose.md`: YAML 1.2 frontmatter between `---` lines, then a Markdown body.
   `name` equals the file stem. Parsing uses a YAML 1.2 core-schema loader; duplicate and non-string keys are
   errors.
2. **Frontmatter.**
   - The top level is OpenProse-compatible: `name` (slug, `NAME_PATTERN`, ≤64), `kind: responsibility`, optional
     `id` (minted, Crockford base32), optional `version` (semver) and `description` (≤1024). `x-*` keys are
     ignored.
   - Every hz key is nested under `hz:`: `schema` (integer), `title`, `owner`, `maintainers`,
     `scope: personal|team`, `tags`, `schedule`, `freshness`, `on`, `inputs`, `outputs`, `publishes`, `agent`,
     `tools`, `timeout`, `retries`, `concurrency`, `catch_up`, `cost`, `idempotency`, `overrides`.
   - Keys that exist on `JobLet` (ADR 0020) carry the same names and meaning.
3. **Body.**
   - Required: `### Goal` (one sentence describing a state), `### Maintains` (one item per output; `####` facets
     with Material and Immaterial notes) and `### Done when` (postconditions).
   - Optional: `### Requires` (one item per input), `### Continuity`, `### Steps`, `### Invariants`, `### Tools`,
     `### Description`.
   - Retired OpenProse sections (`### Criteria`, …) and `### Execution` are rejected.
4. **One source of truth per fact.**
   - The frontmatter is authoritative for the kernel.
   - Validation fails when frontmatter inputs, outputs or facets disagree with the backticked items and `####`
     headings in the body.
5. **Models and schema.**
   - Pydantic models (`ContractFrontmatter`, `Hz`, `Input`, `Output`, `Publish`) are the source of truth.
   - The JSON Schema is generated and committed as `docs/schemas/contract-frontmatter-<n>.json`, and a test fails
     on drift.
   - Validation errors carry file, line, column, field path, a stable `HZC` code and a hint, and come as JSON with
     `--json`.
   - `hz.schema` versions the format. Pure migration functions upgrade older files before validation, and
     `hz workflow migrate` rewrites them (by pull request for team files).
6. **Compilation.**
   - A new `ContractLet(name, origin, path, text, contract, source_fingerprint, contract_fingerprint)` is compiled
     at boot, without a model, into one `JobLet` whose `func` runs one bounded agent session with read-only tools.
   - Its inputs are compiled into shared `WatchLet` instances keyed by canonical source.
   - `JobLet` gains `fingerprint` (the contract fingerprint for contracts, `code_version` for Python jobs),
     `freshness` and `cost`.
7. **The graph.**
   - Edges come only from explicit `contract:<namespace>/<name>#<facet>` inputs. There is no semantic matching.
   - Downstream contracts see published truth unless they declare `stage: draft`.
   - The graph must be acyclic: cycles are reported and the affected contracts disabled.
   - Team contracts cannot depend on personal ones.
8. **Fingerprints.** All are SHA-256 over RFC 8785 canonical JSON of normalized text.
   - `contract_fingerprint` covers intent only: kind, inputs, outputs, the publish actions and targets, tools, and
     one hash per behavior-bearing section. It is computed from the migrated model with defaults filled in.
   - It excludes identity, display, scheduling, agent hints, limits, policy references, `### Continuity` and
     comments.
   - `source_fingerprint` covers the exact canonical text and pins embedded workflows.
   - The memo key is `(contract_fingerprint, input facet fingerprints, freshness_epoch)`. `schedule` only wakes a
     job; `freshness` advances the epoch.
9. **Approvals.**
   - Every `publishes` entry names a write `OperationLet` and needs approval.
   - `auto_approve` names a policy but grants nothing. It takes effect only if the owned policy in the state
     repository matches.
   - It is ignored for the first proposal after an intent change, and for corrections to something already
     published.
10. **Locations.**
    - Contracts live in modules and plugins (namespace = module name), in the team state repository's
      `workflows/` (`team`), in project `.heinzel/workflows/` (`project`), in the user config directory (`me`),
      or embedded and registered (`doc`).
    - Qualified names are `<namespace>:<name>`. A bare name resolves `team` > `project` > `me` > module.
    - Replacing a contract requires an explicit `hz.overrides`. Nothing local overrides a `team` contract, and
      local layers allow only `scope: personal`.
11. **Embedding.** The canonical text is the file text. A leading fenced `yaml` block is accepted in place of
    `---`, and headings are matched relative to the shallowest level. Both forms are converted to the canonical
    form before fingerprinting.
12. **Operations.** A `workflow` module provides `hz workflow list | show | validate | new | graph | fingerprint |
    run --dry-run | migrate | export --prose`.

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
- Bad: body and frontmatter can disagree, so validation must check both. `hz workflow validate --fix` and
  agent-drafted pull requests keep the cost low.
- Bad: new work:
  - `ContractLet`, the parser (ruamel.yaml, markdown-it-py), the models and the generated schema;
  - the compiler to `JobLet` and `WatchLet`;
  - graph checks and the fingerprint functions, with golden tests;
  - the `workflow` module, the `HZC` diagnostics and a `HealthLet` for invalid contracts.
- Neutral: Material and Immaterial notes are honored by the render in v1. A deterministic canonicalizer from those
  notes is follow-up work.

### Confirmation

Partly, for the part built in 0.2 (see the implementation status under More Information): the strict YAML loader,
the frontmatter schema, the body checks and the `HZC` diagnostics with their lines
(`tests/unit/modules/workflow_contracts_test.py`); discovery, name collisions, publish checks, compilation into
daemon jobs, the packet with inputs fetched through capability handles, drafts and their records, and the health
check, with test-only adapters and a fake agent runner (`tests/unit/modules/workflow_service_test.py`); `hz workflow`
through the CLI head, a receipted daemon run, and a hostile project `hz.toml` that can't point discovery at its own
contracts (`tests/integration/workflow_test.py`); the daemon running compiled jobs
(`tests/unit/modules/daemon_service_test.py`) and the kernel's read-only `handle_for`
(`tests/unit/kernel/bindings_test.py`); the committed JSON Schema regenerated and compared by `make docs` and
`make check`. No test runs a real agent. Still planned: golden tests for fingerprints, the contract graph, and
`hz workflow check` in a team repository's CI template.

## More Information

- Amends [ADR 0010](0010-towards-a-reactive-workflow-engine.md) (contract format) and refines the `ContractLet` of
  [ADR 0020](0020-daemon-head.md).
- Research, schema and examples: [docs/research/workflow-contracts.md](../research/workflow-contracts.md).
- Disagreements with the other proposals, to settle before any of them is accepted (status as of 2026-10-24):
  - **Data file format.** Settled for contracts: YAML frontmatter, read with a loader that resolves YAML 1.2 core
    scalars (below). The director's recommendation for [ADR 0023](0023-inventory.md) and
    [ADR 0024](0024-people-and-org.md): data files people write are YAML too, and TOML stays the format of
    configuration ([ADR 0012](0012-layered-configuration.md)); those ADRs decide when they are built.
  - **Two canonicalization rules.** Open: §11 here and `canonical-v1` in [ADR 0022](0022-embedded-workflows.md) both
    define the canonical text that `source_fingerprint` and the embedded pin hash; they must be one function. 0.2
    computes no fingerprints of contracts, so nothing depends on either yet.
  - **People selectors.** Open: `owner` and `maintainers` are refused in 0.2 and wait for ADR 0024's selector grammar.
  - **Naming.** Settled: when project contracts come, they live in `.hz/workflows/`, after
    [ADR 0016](0016-name-heinzelmaennchen.md); `.heinzel/workflows/` is not used. 0.2 doesn't read project contracts.
  - **Runner enum.** Settled by [ADR 0029](0029-agent-runners-are-capability-adapters.md): the runner and the model
    are configuration; `hz.agent.runner`, `model`, `effort` and `max_turns` are refused with a message saying so.
    Inputs and publishes name capabilities and operations, never vendors
    ([ADR 0028](0028-integrations-are-capability-adapters.md)): an input is a capability's read method, not a URI.
  - **Tools.** Settled for 0.2 (option a): render packets pre-fetch inputs through capability handles and the agent
    gets no hz tools; `hz.tools` is deferred.

**What 0.2 implements.** The status stays proposed: fingerprints, memo keys, the graph, watches, team scope,
approvals, migration and embedding are not built. Built in the `workflow` module, with the choices made while building
it (director's decisions, 2026-10-24):

- **File and parser.** `<name>.prose.md`, frontmatter between `---` lines, `name` equal to the file stem (`HZC010`).
  PyYAML, already locked, parses it, not ruamel.yaml: a loader keeps only YAML 1.2 core scalars (`true`/`false`,
  decimal and hex integers, floats, null), so `on`, `yes`, `no`, dates and `1:20` stay strings; numbers with a leading
  zero stay strings too, so an id is never read as a number. Duplicate and non-string keys are `HZC004`; Python tags
  fail like any YAML error (`HZC001`). Every key keeps its line, so every diagnostic is `file:line  CODE  key  message`
  with a hint, and `--json` gives the same as data.
- **The 0.2 frontmatter.** Top level: `name`, `kind: responsibility`, `description`, optional `id` and `version`;
  `x-*` keys are ignored. Under `hz:`: `schema: 1`, `title`, `scope` (only `personal`), `schedule` (five cron fields or
  an alias, local time; none means by hand only), `requires`, `inputs`, `outputs`, `publishes`, `agent`, `timeout`
  (the agent session, at most 2h; default 10m), `retries` (0 to 3, default 0, since a retry spends again) and
  `catch_up`. Every other key this ADR names is refused with `HZC023`, saying why and what to use instead; unknown
  keys are `HZC002`. `hz.schema` newer than 1 asks for `hz system update` (`HZC003`). Models with described fields are
  the source of truth; `docs/schemas/contract-frontmatter-1.json` is generated from them by `make docs`, and
  `make check` fails when it drifts.
- **Requirements.** `requires` lists `<capability>:read`; `agent-runner:read` is implied. A contract never pins an
  adapter (configuration decides, [ADR 0028](0028-integrations-are-capability-adapters.md)) and never writes: `write`
  is declared only on the publish operation, and a contract's `publishes` names that operation. A capability required
  twice is an error.
- **Inputs** are `{read: <capability>.<method>, args: {...}, window: 7d}`: a read method of a required capability,
  checked against the protocol's signature (arguments known, present and of the right type; `window` exactly when the
  method takes a `Window`). The window ends when the run starts; schedule slots and memo keys come later.
- **Outputs and publishes.** Exactly one output, `format: markdown | json`; `target` and facets are deferred.
  `publishes: [{action: <module>.<operation>, output: <name>}]` names a publish operation: a read-only one is an
  error (`HZC013`), one no enabled module provides a warning (`HZC012`), so a team's CI needn't install every plugin.
  Nothing is published: drafts wait for `hz inbox` ([ADR 0020](0020-daemon-head.md)).
- **Agent hints** map to the `AgentRunner` call: `tools` is the read-only tool profile in the runner's naming (empty:
  its default), `max_usd` the budget, `hz.timeout` the session's timeout. The daemon job's timeout is that plus two
  minutes for fetching inputs and saving the draft.
- **Body.** `### Goal` (non-empty), `### Done when` (with list items) and `### Maintains` are required, `### Requires`
  when there are inputs; their backticked items must match the keys of `hz.inputs` and `hz.outputs` (`HZC103`).
  Retired OpenProse sections and `### Tools` are errors (`HZC106`), unknown sections warnings (`HZC105`). Headings are
  matched at `###` only, ignoring fenced code; relative levels for embedded contracts are deferred. HTML comments are
  authors' notes and are stripped before the agent sees the body. Facets and Material/Immaterial checks are deferred.
- **Locations and trust.** Two namespaces: `me` is `[workflow] user_dir` (default `<config dir>/workflows`), `team` is
  `workflows/` in a checkout of the team's state repository, `[workflow] team_repository`. Like the bindings and the
  runners' settings (ADR 0028, 0029), a project `hz.toml` can't set `user_dir`, `team_repository` or `schedule_team`,
  and doctor names the keys it tried: a repository someone else controls must not decide which prompts your daemon runs
  with your agent and capabilities. Project contracts are not read in 0.2. Whatever a contract says, it is data: it
  gets read handles for what it declared, through the adapters configuration binds, and nothing else. A bare name
  resolves `team` before `me`, and a local contract can't take a team contract's name (`HZC301`); `hz.overrides` is
  deferred.
- **Team contracts on personal daemons.** Without leases, every member's daemon would render (and pay for) every team
  contract. So team contracts run by hand (`hz workflow run`), and on your daemon only when named in
  `[workflow] schedule_team`; your own contracts with a schedule always run.
- **Compilation without a new let.** Contracts are data read when the kernel boots, not code declared on import, so
  no `ContractLet` was added. The module provides the kernel's `CompiledJobs` service; the daemon runs its jobs beside
  the declared ones as `workflow:<name>`. Each job acts as `Requester(module="workflow", kind="job", name=<name>)` with
  the contract's requirements and gets handles through `Kernel.handle_for`, which refuses publishing requesters until
  approvals exist. Compiled jobs are not in `hz system capability` yet; `hz workflow show` and the module's health
  check report their unmet requirements instead. Making them requesters in the matrix needs the kernel to compile them
  before building it: follow-up work.
- **Render.** Fetch every input through its handle (an adapter's failure becomes the run's error), build the packet,
  run the agent once, save the draft. The packet's instructions are the body (the runner puts them in the system
  prompt); its prompt states the task and carries each input as JSON in an `<input>` element, framed as data and with
  `</` escaped, so an input can't close its element. The agent works in a new, empty private directory, so its
  read-only tools see nothing of the user's. A run that doesn't complete, or a JSON draft that doesn't parse, fails
  without a draft. Drafts go to `<data_dir>/workflow/drafts/<name>/<UTC time>.md` (or `.json`), with a
  `.record.json` holding the packet, the SHA-256 of each input and the agent's report; files 0600, directories 0700,
  `data_dir` from user scope like the daemon's.
- **Receipts.** A daemon run (scheduled, or `hz system daemon tick --job workflow:<name>`) writes the daemon's chained
  receipt with the draft's path and hash, the input hashes and the agent's report. `hz workflow run` saves the draft
  and its record but writes no ledger receipt; receipted manual runs go through the daemon.
- **Operations.** `hz workflow list`, `show <name>` (schedule, daemon job or why there is none, requirements with the
  bound adapters and fixes, inputs, outputs, publishes, limits, diagnostics), `check [--path]` (everything found, or a
  file or directory searched recursively; exits with 1 on errors) and `run <name> [--dry-run]` (the dry run fetches
  the inputs and prints the packet, no agent runs). The path is an option because heads have no optional positional
  arguments. `validate`, `new`, `graph`, `fingerprint`, `migrate` and `export` are not built.
