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

Not yet: proposed. When implemented: the drift test for the committed JSON Schema, golden tests for fingerprints,
and `hz workflow validate` in CI.

## More Information

- Amends [ADR 0010](0010-towards-a-reactive-workflow-engine.md) (contract format) and refines the `ContractLet` of
  [ADR 0020](0020-daemon-head.md).
- Research, schema and examples: [docs/research/workflow-contracts.md](../research/workflow-contracts.md).
- Disagreements with the other proposals, to settle before any of them is accepted:
  - **Data file format.** This ADR chooses YAML for frontmatter; [ADR 0023](0023-inventory.md) declares inventory
    files in YAML but leaves that open until this ADR settles; [ADR 0024](0024-people-and-org.md) declares people
    files in TOML, citing [ADR 0012](0012-layered-configuration.md), which covers configuration, not data files.
  - **Two canonicalization rules.** §11 here and `canonical-v1` in [ADR 0022](0022-embedded-workflows.md) both define
    the canonical text that `source_fingerprint` and the embedded pin hash; they must be one function.
  - **People selectors.** `owner` and `maintainers` here have no selector grammar yet; ADR 0024 defers its selector
    grammar to this ADR.
  - **Naming.** The project directory `.heinzel/workflows/` departs from [ADR 0016](0016-name-heinzelmaennchen.md),
    under which what users type or see uses `hz` (`hz.toml`, config directory `hz`).
  - **Runner enum.** `hz.agent.runner` as a fixed enum conflicts with [ADR 0029](0029-agent-runners-are-capability-adapters.md),
    under which the runner is configuration; and `inputs`, `outputs` and `publishes` should name capabilities, not
    vendors ([ADR 0028](0028-integrations-are-capability-adapters.md)).
