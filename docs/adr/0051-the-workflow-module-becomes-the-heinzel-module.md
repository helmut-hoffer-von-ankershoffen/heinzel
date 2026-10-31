---
status: accepted
date: 2026-10-24
decision-makers: maintainer (names and direction), architect (plan)
amends: 0021, 0038, 0041, 0045
amends-0021: operation names
amends-0038: hz inbox becomes hz drafts and hz approve
amends-0041: the everyday set
amends-0045: heinzel.* settings in the never-run policy
related: 0002, 0003, 0018, 0019, 0020, 0026, 0037, 0039, 0049, 0050, 0052
implemented-by: 94
---

# 0051: The workflow module becomes the heinzel module

## Context and Problem Statement

The `workflow` module (`src/heinzel/modules/workflow`, 4,600 lines) reads contracts, compiles them into daemon jobs,
renders drafts and keeps their records; its commands are `hz workflow list | new | show | check | run | drafts | sent
| graph | playbooks | discover | prune`. [ADR 0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md)
makes the durable Heinzelmännchen the thing a user has, with one contract each and one session each, and the maintainer
decided: rename the module to `heinzel`, a built-in module that is *what hz is*, not a plugin; the kernel gets only bare
primitives (a durable session store); the CLI and MCP offer `hz heinzel list | show | chat | wake | new`, `hz drafts`
and `hz approve`, call the services directly, and the HTTP API exists only for the UI. How is the module renamed without
breaking the people who run `hz workflow` today, what moves to the kernel, and what becomes of existing contract files
and run records?

## Decision Drivers

- One name for one thing (tenet 5): a user has Heinzelmännchen, each with a contract; "workflow" was the graph those
  contracts form ([ADR 0021](0021-workflow-contracts.md) §14) and stays that word for the graph only.
- Kernel stays small and ignorant ([ADR 0002](0002-modulith-with-kernel-modules-and-lets.md)): it knows no
  Heinzelmännchen, no contract, no draft; the module owns the concept.
- Heads are views on operations ([ADR 0003](0003-heads-are-views-on-operations.md)); the new names must fall out of the
  module and operation names, with `heads=` restrictions, not special cases in heads.
- No user left behind: a renamed command or setting keeps working for one minor release with a message
  ([ADR 0026](0026-plugin-api-versioned-with-semver.md)'s deprecation cycle applied to the CLI and configuration).
- The thin slice's contracts, drafts and receipts exist on machines today; migration is idempotent and loses no lineage.

## Considered Options

- **Rename the package and module to `heinzel`, add two single-operation modules `drafts` and `approve`, keep `hz
  workflow …` and `[workflow]` as deprecated aliases for one minor release, move only a `SessionStore` primitive into
  the kernel, migrate files with one idempotent operation** (chosen)
- **Keep `workflow` and add `heinzel` beside it**: two modules over one concept, two stores, and every operation would
  have to say which; the user would see both in help.
- **A `heinzel` plugin in `plugins/`**: the maintainer's decision is explicit (it is what hz is); a plugin is optional,
  and the chat, the daemon's wakes and the web app's home need it in the core.
- **Put the Heinzelmännchen model into the kernel** (ids, contract versions, turns): a head-specific and module-specific
  concept in the layer that must know none; the kernel gets the store, not the meaning.
- **`hz heinzel drafts` and `hz heinzel approve`** instead of top-level `hz drafts` and `hz approve`: the maintainer
  named the top-level forms; they are the two commands a user runs most, and ADR 0018's rule reserves `hz system` for
  administration, not the top level for one module.
- **Head-level path overrides** (`@module.operation(path="drafts")`): a new kernel concept to spell two commands; two
  tiny modules that collapse into single commands do it with the rule that exists (ARCHITECTURE: "a module whose only
  operation shares its name collapses into a single command").
- **A hard rename with no aliases**: 0.x allows it, but the daemon's unit files and the team repositories' CI call
  `hz workflow check`; a release that breaks them silently is against tenet 1.

## Decision Outcome

Chosen option: **`heinzel.modules.workflow` becomes `heinzel.modules.heinzel`, the module `heinzel`; two
single-operation built-in modules give `hz drafts` and `hz approve`; the old commands, settings and job names work as
deprecated aliases for one minor release; the kernel gains only `SessionStore`; `hz heinzel migrate` turns today's
contracts, drafts and records into Heinzelmännchen and turns, once.** Checkable statements:

1. **The rename.** `src/heinzel/modules/workflow/` → `src/heinzel/modules/heinzel/`, `module = Module("heinzel", "Your
   Heinzelmännchen: durable agents that keep your team's documents true.")`. Settings table `[heinzel]`, environment
   `HZ_HEINZEL__*`; the module can't be disabled, like `system`. Internals keep their files (`_contracts.py`,
   `_models.py`, `_graph.py`, `_playbooks.py`, `_retention.py`, `_measure.py`, `_starter.py`); `_service.py` becomes
   `HeinzelService`; `_render.py` is replaced by `_engine.py` and `_turns.py`
   ([ADR 0050](0050-pydantic-ai-runs-a-turn-a-coding-agent-is-a-handoff.md), ADR 0049). The word "workflow" survives
   only where it means the graph (`hz heinzel graph`, docs/WORKFLOWS.md's section on the graph) and in the team
   repository's `workflows/` directory (5). `docs/WORKFLOWS.md` becomes `docs/HEINZELMAENNCHEN.md`, with a stub at the
   old path for one release; generated docs follow (`make fix`).
2. **Commands and tools.** The module's operations and their heads:

   | Operation | CLI | MCP tool | Heads | Was |
   | --- | --- | --- | --- | --- |
   | `list` (filters `--artifact`, `--state`, `--contract`) | `hz heinzel list` | `heinzel_list` | cli, mcp, web | `workflow list` |
   | `show NAME` | `hz heinzel show` | `heinzel_show` | cli, mcp, web | `workflow show` |
   | `chat NAME [MESSAGE]` | `hz heinzel chat` | `heinzel_chat` | cli, mcp, web | (new; the session, ADR 0049 §4) |
   | `wake NAME [--dry-run]` | `hz heinzel wake` | `heinzel_wake` | cli, mcp | `workflow run` (spends money: never on the unauthenticated REST head, [ADR 0019](0019-mcp-and-rest-heads.md)) |
   | `new [SENTENCE \| --template]` | `hz heinzel new` | `heinzel_new` | cli, mcp, web | `workflow new` (writes a proposed v1, ADR 0049 §2) |
   | `check`, `graph`, `playbooks`, `discover`, `prune`, `migrate`, `reindex`, `retire`, `pause`, `resume` | `hz heinzel …` | `heinzel_…` | as today for each | `workflow check \| graph \| playbooks \| discover \| prune`; `migrate`, `reindex`, `retire`, `pause`, `resume` are new, expert audience |
   | `drafts` (module `drafts`, one operation) | `hz drafts` | `drafts` | cli, mcp, web | `workflow drafts`; the view of ADR 0038 §8 over the index |
   | `approve [DRAFT \| AMENDMENT \| HEINZELMAENNCHEN] [--reject]` (module `approve`, one operation) | `hz approve` | `approve` | cli, web | `hz inbox approve \| reject` (ADR 0038 §8), never built; never on MCP, like `hz chat confirm` (ADR 0045): an agent must not approve |
   | `sent` | `hz heinzel sent` | `heinzel_sent` | cli, mcp | `workflow sent`; folds into `approve --file` once drafts are placed in place, deprecated then |

   `drafts` and `approve` are built-in modules of their own (`heinzel.modules.drafts`, `heinzel.modules.approve`), each
   with one operation of the module's name and no submodules, so every head collapses them (`hz drafts`, tool `drafts`,
   `GET /api/drafts`); they inject `HeinzelService` by type like any service ([ADR 0005](0005-dependency-injection-by-type.md))
   and hold no state. Everyday audience ([ADR 0041](0041-expert-mode-newcomers-see-the-everyday-commands.md)): `hz
   heinzel list | show | chat | new`, `hz drafts`, `hz approve`; the rest is expert. CLI and MCP call the services in
   process as every head does; the REST head exposes nothing of this module (`heads=` lists `web` for the UI's needs
   and never the bare REST head), which is what "the HTTP API exists only for the UI" means here
   ([ADR 0039](0039-a-web-head-a-react-app-served-by-the-api-head.md)).
3. **Aliases and deprecation, one minor release.** `hz workflow <op>` stays as a hidden command group that runs the
   `heinzel` operation of the same meaning (`run` → `wake`, `drafts` → `hz drafts`) and prints one line on stderr
   naming the new command; the MCP head keeps the old tool names as aliases with a `deprecated` note in their
   description. `[workflow]` settings are read as `[heinzel]` when `[heinzel]` doesn't set the key, and `hz system
   doctor` names each key to move (as it does for `team_repository` today); `HZ_WORKFLOW__*` likewise. Daemon job
   names `workflow:<ns>-<name>` become `heinzel:<id>`; the ledger's receipt chains are per job, so `migrate` (6) copies
   each old chain under the new name as its history, with a receipt that names the move, rather than starting the chain
   again. `hz system daemon install` rewrites nothing: the unit calls `hz system daemon tick`, which reads jobs afresh.
   The release after the alias release removes aliases, old settings and the old doc path; the changelog says so twice.
4. **What moves to the kernel: `SessionStore`, nothing else.** `heinzel.kernel._sessions` provides `SessionStore`
   (ADR 0049 §3, §5, §7): `append(stream, kind, data, *, key=None, turn=None)` with chain and fsync, `read(stream,
   since_seq)`, `bodies(stream)` (a turn body's writer and reader), `index` (the derived SQLite index with the rows the
   module declares through a small schema, a `seq` watermark and `rebuild()`), and `expire(stream, before)` for bodies.
   It knows streams, events, keys and bodies; it knows no Heinzelmännchen, contract, draft or turn kind, and it names no
   module. It is a kernel service injected by type, the way `Credentials` is. The `Ledger` of receipts stays in
   `system.daemon` until ADR 0020's `state` module moves it; `SessionStore` doesn't replace it. Nothing else of the
   module enters the kernel: not the models, not the contract parser, not the engine.
5. **Where files live.** Contracts stay `.prose.md` files where they are: `me` in `[heinzel] user_dir` (default
   `<config dir>/heinzel/`, with `<config dir>/workflows/` read as an alias and named by doctor), `team` in the team
   repository's `workflows/` (unchanged: the team repository's schema is the team's, [TEAMS](../TEAMS.md); a later schema
   bump may rename it, and hz will read both). A Heinzelmännchen's session is `<data dir>/heinzel/<id>/` (ADR 0049 §3);
   the file is the contract's *current* text for editing, and `contract/v<n>.prose.md` in the session the approved
   versions. Editing the file is proposing an amendment (ADR 0049 §2): a changed file whose fingerprint differs from the
   current version shows as a pending amendment in `hz drafts` and `hz heinzel show`, and `hz approve` makes it the next
   version; until then the Heinzelmännchen runs the approved version. `<data dir>/workflow/drafts/` is read by `migrate`
   and then left to expire.
6. **Migration: `hz heinzel migrate [--dry-run]`.** Idempotent, run once by the user or by the first `hz heinzel list`
   that finds old state (it then says what it did). For each contract file found: mint an id, create the session with
   `created` and `contract.approved` (v1 = the file's text, approved because the user already runs it; a team contract
   pinned in `schedule_team` keeps its pin as the version's fingerprint), state `asleep` (or `paused` when its pin no
   longer matches). For each draft directory under `<data dir>/workflow/drafts/<ns>-<name>/`: one turn of cause
   `imported` per `<stamp>.record.json`, its `turn.finished` holding the draft's hash, cost, duration and the `sent`
   measurement when there is one, and the record and draft moved into the turn body with their original stamp so they
   expire on the same day they would have. For each ledger chain `workflow:<ns>-<name>`: copied under `heinzel:<id>`
   (3). A second run finds the ids in the index and changes nothing; `--dry-run` lists every file and job it would
   touch. Nothing is deleted by migration; expiry does that on schedule.
7. **Everywhere the old name is written.** `scripts/generate_docs.py`, `docs/MODULES.md`, `CLI_REFERENCE.md` and
   `CONFIGURATION.md` regenerate; the web app's `Contracts` and `Contract` pages become "My Heinzelmännchen" and the
   page per Heinzelmännchen (ADR 0049 §11), with the routes `/contracts*` redirecting for one release; the chat's
   never-run policy ([ADR 0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md)) names `heinzel.*`
   settings in place of `workflow.*`; the `agent-skills/` plugin's `hz` skill names the new commands; the template's
   playbook docs say `hz heinzel new --template`. `scripts/check_confidentiality.py` is unaffected.

### Consequences

- Good: the user's noun and hz's module are one word, and the two most frequent commands are two words long.
- Good: the kernel grows by one primitive it can test alone; the module keeps every concept.
- Good: nobody's unit file, CI step or configuration breaks on the day of the release; everyone learns the new name
  from the old command's one line.
- Bad: a large mechanical diff (imports, tests, docs, the web app) that must land before the behaviour changes of
  ADR 0049 and 0050, or alongside them in one release; the plan below orders it.
- Bad: two module names in the tree for one release (`workflow` as alias) and a receipt chain copied under a new job
  name; `migrate` is the one place that knows both.
- Bad: `heinzel` is also the Python package and the distribution; `heinzel.modules.heinzel` reads oddly in code. The
  module name is what users see (`hz heinzel`, `[heinzel]`), which decides it.
- Neutral: ADR 0021's operation list (§12) and ADR 0038's `hz inbox` are amended, not contradicted: the operations
  keep their meaning under new names, and the inbox was always a view.

### Confirmation

The first slice (the rename, the aliases, `hz heinzel list` and the web app's Heinzelmännchen page) is checked by
`tests/integration/renamed_module_test.py`, `tests/integration/heinzel_test.py`, `tests/unit/kernel/config_test.py`
and `web/src/pages/Heinzelmaennchen.test.tsx`. When the rest is implemented:

- `tests/integration/heinzel_cli_test.py`: every new command through the CLI and MCP; `hz workflow <op>` runs the same
  operation and prints the deprecation line once; `hz drafts` and `hz approve` collapse in every head; `approve` and
  `wake` absent from the REST head and `approve` from MCP (`tests/integration/rest_security_test.py`, `mcp_test.py`).
- `tests/unit/modules/heinzel_settings_test.py`: `[workflow]` keys read as `[heinzel]` with doctor naming them; a
  project `hz.toml` still can't set any of them.
- `tests/unit/modules/heinzel_migrate_test.py`: a fixture `<data dir>/workflow/drafts/` and `receipts/` from 0.3.0
  migrates into sessions and copied chains; the chains verify; a second run changes nothing; `--dry-run` writes nothing;
  a pinned team contract whose file changed migrates `paused`.
- `tests/unit/kernel/session_store_test.py` (ADR 0049) and the import-linter contracts: the kernel still imports no
  module; `heinzel.kernel._sessions` names no Heinzelmännchen (a grep test in `tests/unit/repository_test.py`).
- `tests/unit/documentation_test.py`: the generated docs, the ADR index and the renamed page with its stub.
- `tests/unit/heads/cli_help_test.py`: the everyday commands list `hz heinzel`, `hz drafts`, `hz approve`.

## More Information

- Decided by the maintainer, 2026-10-24: the rename to `heinzel`, a built-in module not a plugin, the kernel getting
  only bare primitives, the command names, "CLI and MCP call the services directly; the HTTP API exists only for the
  UI". Plan by the architect.
- **Amends** [ADR 0021](0021-workflow-contracts.md) §12 (operation names) and §10 (the `me` directory's default path);
  [ADR 0038](0038-drafts-are-approved-in-place.md) §8 (`hz inbox list | show | approve | reject` are `hz drafts` and
  `hz approve [--reject]`); [ADR 0041](0041-expert-mode-newcomers-see-the-everyday-commands.md) (the everyday set);
  [ADR 0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md) (`heinzel.*` in the chat's never-run
  settings). It does not touch [ADR 0018](0018-one-administrative-namespace.md): `hz heinzel`, `hz drafts` and `hz
  approve` are team work at the top level, where ADR 0018 puts it.
- Order of landing (ADR 0049's plan): the rename (this record's 1–3, 5 and 7) is step 2 there and lands before the
  turn engine (step 3); `SessionStore` (4) is step 1; `migrate` (6) grows with steps 2 and 4 (drafts and receipts).
- **Open:** whether `hz heinzel` should also answer without a subcommand (recommendation: as `list`, the way `hz
  system expert` shows the state); whether the team repository's `workflows/` is renamed `heinzel/` at the next team
  schema bump (recommendation: yes, with both read for one release).
