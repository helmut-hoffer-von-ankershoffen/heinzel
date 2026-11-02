---
status: proposed
date: 2026-10-24
implementation: built
decision-makers: maintainer
related: 0021, 0026, 0028, 0032, 0035
amended-by: 0054
implemented-by: 38
---

# 0037: Plugins bring playbooks: asset types with discovery hints, template contracts and recommended settings

*Since 0051 the module is `heinzel`: read `hz workflow` as `hz heinzel` and `[workflow]` as `[heinzel]`.*

## Context and Problem Statement

The first contract worth validating is a team's weekly report: drafted by Friday 17:00 from the week's pull requests,
tracker items and the previous report, edited and sent by the person who owns it. The core knows nothing about
weekly reports, because: "Playbooks as plugins. A plugin can bring adapters, a playbook (asset types
with discovery hints + template contracts) and config defaults. Setup proposes assets found by those hints and offers
the template contracts." Layers and playbooks are plugins (an ADR on layers as plugins is being written alongside
this one); this record decides only what a playbook is, how the core collects it, and who acts on it.

## Decision Drivers

- The core stays generic (tenet 9): everything specific to a kind of team work, a company or a vendor is a plugin.
- A contract is an invariant about one asset ([ADR 0021](0021-workflow-contracts.md)); a playbook names the asset
  types its contracts keep true, so a later version can write a draft where the asset lives.
- Nothing runs, reads more than the person can, or changes configuration by itself: hints only read through the
  capabilities configuration binds ([ADR 0028](0028-integrations-are-capability-adapters.md)), and settings are
  proposals the person accepts ([ADR 0032](0032-modules-declare-interactive-setup-steps.md)).
- Declared as data, so it can be listed, checked and tested without a model or a network.

## Considered Options

- **A `PlaybookLet` declared with `module.playbook(...)`, consumed by the `workflow` module, walked by a setup step**
  (chosen)
- **Template contracts shipped in the core** (`hz workflow new --template weekly-report`): the first slice did this;
  it puts a kind of team work into the core, against the maintainer's decision.
- **Playbooks as files a plugin's package carries, found by convention** (`<package>/playbook.yaml`): another format
  and loader beside contracts, with no place for typed validation at import and nothing the capability matrix or the
  kernel's let listing would see.
- **Each plugin writes its own setup step and templates by hand**: every plugin re-implements discovery, filling
  placeholders and applying defaults, and `hz workflow` can't list what is on offer.

## Decision Outcome

Chosen option: **a `PlaybookLet` declared with `module.playbook(...)`**, because it keeps playbooks typed data the
kernel collects like every other let, leaves acting on them to the module that owns contracts, and lets setup offer
them without the core knowing what they are about.

- **Declaration.** `module.playbook(summary, assets=[AssetType(...)], templates=[Path | ContractTemplate],
  defaults={"<section>.<key>": value})` adds one `PlaybookLet` per module, named after it. Templates are
  `*.prose.md` files (or directories of them) read at import. The shape is checked when declared: names are slugs,
  a hint names a capability's read method (never `agent-runner`), windows parse, patterns compile, every placeholder
  and `kept_by` names something the playbook declares. A hint's arguments are checked when it runs, like a contract
  input's, and a mismatch is reported as the playbook's mistake.
- **Asset types and hints.** An `AssetType` has a name, a description, `DiscoveryHint`s and optionally `kept_by`, the
  template whose output keeps it true. A hint is a contract input's shape (`read`, `args`, `window`, one window grammar
  in the kernel: `30d`, `last 30d` or `next 14d`, at most 366 days) plus `match`, a
  pattern on each returned item's `title`; a matching item's `ref`, `key`, `id` or `url` is the candidate's reference.
- **Templates.** A `ContractTemplate` is a contract's text with `{{asset:<type>}}` placeholders, which must sit in
  double-quoted strings in the frontmatter (refused anywhere else: unquoted they could add keys, in the body they
  would reach the agent's instructions). A template with an active `schedule` is refused: when a contract runs on the
  daemon is the person's decision. `hz workflow new <name> --template <module>:<template> --asset <type>=<ref>` fills
  the placeholders in one pass, each reference cleaned and JSON-escaped, sets the name in the frontmatter, and refuses
  a result `hz workflow check` would reject. A placeholder left unfilled is an error (`HZC025`) until the person names
  the asset, so the contract isn't ready.
- **Consumer.** The built-in `workflow` module consumes the let: contracts are its concept (ADR 0021), and a
  playbook is inert when it is disabled, because the setup step's `PlaybookSetup` is then unavailable. It lists
  playbooks (`hz workflow playbooks`), runs hints (`hz workflow discover`, CLI and MCP only) and provides
  `PlaybookSetup`. One failing hint is its own problem; an unbound capability is reported once, with its fix.
- **Reads declared by data.** Hints, like contract inputs, are reads declared by data rather than code. They go
  through `Kernel.handle_for` as a read-only job of `workflow` named `discover-<module>` that declares just that
  capability; so does reading an approved version (`approved-version`). This widens the seam ADR 0021 describes from
  "the compiling module's jobs" to "the compiling module's data-declared reads"; they get read modes only, never an
  adapter pin, and, unlike scheduled contracts, don't appear in the capability matrix.
- **Recommended settings.** Only capability bindings (never `agent_runner`) and the module's own `<module>.*`
  settings: a playbook is data and can't recommend what runs code, what the agent may do, what it costs or where
  contracts come from. Each is applied only on its own yes, through `ConfigEditor`; one that is refused is reported
  and the others are still offered.
- **Setup.** Unless `setup=False`, `module.playbook` declares the module's setup step: `hz setup <module>` and
  `hz setup` offer each recommended setting, the candidates for each asset type (or "fill it in later"), and each
  template, written only when confirmed under a name the person may change; an existing contract is left alone. A
  module with its own step passes `setup=False` and asks for `PlaybookSetup` in it.
- **The asset a contract keeps.** `kept_by` names the template whose output keeps an asset true. Drafts stay local
  in this version; the success measure of ADR 0021 (`hz workflow sent --ref`) reads the approved version from where
  the asset lives.

### Consequences

- Good: a plugin such as a company's reference plugin brings adapters, a playbook and defaults in a few lines; the
  core ships no team-work templates and stays usable anywhere.
- Good: hints can't read more than the person bound, can't write, and run only when asked; defaults apply only on yes.
- Bad: today's capability protocols list items within a known container (a repository, a project, a document id), so
  hints can confirm and narrow, not search: a title pattern over all documents or a folder needs listing reads on
  `documents`, a protocol change with its own ADR.
- Bad: settings applied during setup count from the next command, so the same setup run can't yet discover through a
  binding it just wrote; it says so.
- Neutral: new public API, additive ([ADR 0026](0026-plugin-api-versioned-with-semver.md)): `PlaybookLet`,
  `AssetType`, `DiscoveryHint`, `ContractTemplate` and `Module.playbook` in `heinzel`; `PlaybookSetup` in
  `heinzel.kernel`.

### Confirmation

- `tests/unit/kernel/playbooks_test.py`: every check at declaration (an active schedule, placeholders outside
  double-quoted frontmatter strings, settings a playbook may not recommend), filling placeholders in one pass,
  reading template files, the setup step `module.playbook` declares and its clash with a module's own step.
- `tests/unit/modules/heinzel_playbooks_test.py`: discovery (matches, references, the candidate cap, a failing
  adapter, arguments that don't fit, an unbound capability), template lookup, a template that fails its checks, the
  setup walk writing only what is accepted.
- `tests/integration/playbook_test.py`: a test plugin with adapters, a weekly report playbook and defaults, end to end
  through the CLI: playbooks, discover, setup answered on stdin, new from the template, a dry run that costs nothing,
  the daemon drafting on Friday 17:00 in a pinned time zone with lineage, and the success measure in `show`.

## More Information

- Validation slice: one contract (draft ready
  Friday 17:00, real inputs through vendor CLIs, a local draft the person edits and sends, measured after three
  Fridays by time saved and how much was changed). The weekly report playbook itself lives in a reference plugin.
- Planned next: approval in place. The draft is written where the asset lives (this week's section of the rolling
  report document) with a draft tag; the person edits it and removes the tag to approve; hz reads the approver from
  revision history and diffs draft against approved. `kept_by` and `sent --ref` are the seams for it; the contract
  will need to record the asset it keeps (e.g. `hz.keeps: {weekly-report: "<ref>"}`, filled from the template), so
  `sent` can read it without `--ref`.
- Open: listing reads for `documents` (title patterns, folders) so hints can search; showing data-declared reads
  (hints, approved versions) in the capability matrix.
- Numbering: 0036 is left for the record being written alongside this one (layers as plugins).
