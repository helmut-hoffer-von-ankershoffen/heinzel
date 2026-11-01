---
status: proposed
date: 2026-10-23
implementation: planned
related: 0010, 0020, 0021, 0022, 0024
amended-by: 0052
---

# 0023: The inventory is compiled from declared, discovered and plugin-contributed entries and published as graphs

*Not implemented at 0.9.9; the inventory is not built.*

## Context and Problem Statement

Contracts wire workflows through `Requires` and `Maintains` (ADR 0010, ADR 0021). Daemons read and maintain
documents, sheets, tracker projects, repositories and channels in several systems (ADR 0020). Workflows also hide
inside artefacts (ADR 0022). Nobody can yet answer:

- what a team's workflows depend on, and in what order they run;
- who owns each artefact and who approves each workflow;
- which systems Heinzelmännchen touches;
- whether what runs matches what was declared.

Without that map, cycles and two writers on one artefact go unnoticed; owners go missing, which breaks tenet 4; and
a new team member or an agent has no overview.

## Decision Drivers

- Receipts already record the sources and publish references of every run (ADR 0020 §8), so runtime lineage exists.
  A declared model to compare it with does not.
- The state repository (ADR 0020 §5) is the natural home.
- Views must be readable in GitHub Markdown without adding heavy dependencies to the core.

## Considered Options

- **An inventory compiled in the state repository from declared, derived, discovered and observed entries,
  rendered as text graphs** (chosen)
- **Backstage as the catalog:** mature model and portal, but it needs a server and a database and is
  software-centric. We borrow its descriptor envelope and relations, and may export `catalog-info.yaml` later.
- **A SaaS catalog (Port, OpsLevel, LeanIX):** contradicts "open and independent" and adds a second source of
  truth next to the state repository.
- **Only derived plus observed, with no declared files:** misses artefacts that no contract names yet and owners
  that code cannot know, and gives drift nothing to compare against.
- **Auto-merging discovered entries:** turns anyone who can edit a shared folder or a tab into an editor of team
  configuration, against ADR 0020 §9.
- **TOML for declared files:** consistent with `control.toml` and the registry, and the parser is already a
  dependency, but it is awkward for lists of typed entries and does not align with ADR 0021's frontmatter or
  Backstage. This remains open until ADR 0021 settles.
- **Graphviz or `mmdc` as a hard dependency:** better images, but native or Node-and-Chromium installs on every
  machine. Kept optional.
- **A pure-Python layout engine (`grandalf`, `networkx`):** weak layouts, or matplotlib's weight, for little gain
  over text output plus an optional `dot`.
- **A public Kroki instance for rendering:** would send internal URLs and names to a third party.
- **A separate `hz landscape` module:** a second module over the same model. A view keeps one model and one place
  to validate it.

## Decision Outcome

Chosen option (proposed): **the inventory is a typed graph compiled from four origins into a lock file in the state
repository and published as text graphs**, because it gives one map to validate in CI without a server or new
graph dependencies.

1. **Model.** The inventory is a graph of `artefact`, `workflow`, `system` and optional `domain` entities, plus
   `group` and `person` refs resolved by ADR 0024.
   - Edges are typed, with inverses: `reads`, `maintains`, `triggers`, `owns`, `approves`, `hostedIn`, `uses`,
     `embeddedIn`, `partOf`.
   - Refs are `kind:name` or `kind:namespace/name`.
   - Artefacts and systems carry a canonical `uri`, normalised by the connector for their system. The canonical
     `uri` is the merge key.
   - Every artefact, workflow and system has an owner.
2. **Storage.** In the team state repository:
   - Declared entries live in `inventory/**/*.yaml` (Backstage-style `apiVersion/kind/metadata/spec`), changed by
     pull request, with corrections in `inventory/overrides.yaml`.
   - `inventory.lock.json` is the compiled snapshot: canonical JSON with a fingerprint per entity and for the whole
     inventory.
   - `INVENTORY.md` is generated.
   - `workflows/registry.toml` remains the trust gate for embedded workflows. The inventory reads it and does not
     replace it.
3. **Four origins, one merge.** Each entity and edge records its origins.
   - `declared`: the YAML files and the registry. Authoritative.
   - `derived`: `ContractLet`, `JobLet` and `WatchLet` in the running kernel. Authoritative for what code declares.
   - `discovered`: from `InventoryLet` providers. Data, not configuration: discovery only opens registration
     proposals in the approval inbox, and a pull request turns them into declared entries (ADR 0020 §9).
   - `observed`: from receipts.

   Attribute precedence is declared, then overrides, then derived, then discovered. Conflicting owners are errors.
   The edge set is the union of all origins, and an edge seen from only one side (only declared or only observed)
   is reported as drift.
4. **A new let kind.** `InventoryLet(name, provider, mode="static"|"discover", systems, canonicalize)` is declared
   with `@module.inventory`. The kernel stores it uninterpreted. The `inventory` module consumes
   `kernel.lets(InventoryLet)`, `ContractLet`, `JobLet` and `WatchLet`. Heads are unaffected.
5. **Commands.** `inventory` is a top-level module (ADR 0018). Its operations are `compile [--check]`,
   `validate [--online] [--strict]`, `list`, `show REF`, `diff [BASE] [HEAD]`,
   `graph --view workflows|landscape|ownership|lineage --format mermaid|dot|json|d2|html|svg`, `discover` and
   `publish`.
   - There is no separate `landscape` module: the landscape is a view.
   - `discover` and `publish` change state. `publish` is a publish operation subject to approval (ADR 0020 §7).
6. **Validation.** `validate` checks schema, duplicates, dangling refs, and cycles in the derived workflow DAG
   (stdlib `graphlib`). It also checks single writer per artefact, owners and approvers, unregistered embedded
   workflows, staleness (`last_seen`, `stale_after`, tombstones), declared-versus-observed drift and publication
   classification. It returns `Check`s and is also a `HealthLet`. `compile --check` fails CI when the lock or the
   generated views are out of date, as `scripts/generate_docs.py --check` does.
7. **Rendering.**
   - The core emits text only: Mermaid, DOT, D2, a JSON node-link graph, and a single-file HTML viewer that loads
     pinned client-side libraries.
   - SVG and PNG use `dot`, `d2` or `mmdc` when found on PATH. They are reported by `doctor` and never required. No
     new Python dependency for graphs.
   - Inline Mermaid is refused above 400 edges or 40,000 characters, below Mermaid's defaults of 500 and 50,000, in
     favour of `--focus`, `--group-by` or the HTML view.
   - Every label is escaped.
8. **Publishing and freshness.**
   - The default target is a pull request to the state repository (lock plus `INVENTORY.md`).
   - Private GitHub Pages, Google Docs and Confluence are connector targets, gated by classification.
   - A team-scoped `inventory-refresh` contract (daily, and on changes to inventory, contracts and registry)
     compiles, discovers and publishes. The inventory fingerprint is in its memo key, so an unchanged inventory
     publishes nothing.

### Consequences

- Good: teams get one map of workflows, artefacts, systems and owners. Agents get it through MCP tools for free
  (ADR 0019).
- Good: cycles, double writers, missing owners and drift are found by CI and `doctor`, not in production.
- Good: plugins describe the systems they bring, and how to canonicalise their URIs, through one let.
- Bad: the core needs a safe YAML parser, shared with ADR 0021's frontmatter, if 0021 adopts YAML.
- Bad: new work:
  - `InventoryLet` and the `inventory` module with its pydantic models;
  - canonicalisers in the connector plugins;
  - the renderers (text) and the HTML template;
  - the `inventory-refresh` contract;
  - a state-repo CI step;
  - golden-file tests for the Mermaid, DOT and lock output.
- Bad: the inventory is sensitive: publication targets must be private, and people appear only as ADR 0024 refs.
- Bad: Google Docs cannot embed SVG, or images that are not publicly reachable, so Doc targets get tables and a
  link.

### Confirmation

Not yet: proposed. When implemented: `hz inventory compile --check` in the state repository's CI, `validate` as a
`HealthLet`, and golden-file tests for the Mermaid, DOT and lock output.

## More Information

- Builds on [ADR 0010](0010-towards-a-reactive-workflow-engine.md) and [ADR 0020](0020-daemon-head.md). Consumes
  the contract format of [ADR 0021](0021-workflow-contracts.md), the embedded workflows of
  [ADR 0022](0022-embedded-workflows.md) and the people and org model of [ADR 0024](0024-people-and-org.md) without
  defining them.
- Research: [docs/research/inventory.md](../research/inventory.md).
- Disagreements with the other proposals, to settle before any of them is accepted:
  - **One `inventory` module.** ADR 0022 gives the same module `crawl`, `show`, `register`, `unregister` (and
    `add <url>`); this ADR gives it `compile`, `validate`, `list`, `show`, `diff`, `graph`, `discover`, `publish`.
    They are one module with the union of these operations; `discover` here and `crawl` there overlap and should
    become one operation.
  - **Discovery by plugins.** `InventoryLet(mode="discover")` here and ADR 0022's `DocumentConnector.discover`
    describe the same capability; one mechanism should serve both.
  - **Data file format.** Declared inventory files are YAML here (open until ADR 0021 settles); ADR 0024 declares
    people files in TOML; the registry and `control.toml` are TOML.
