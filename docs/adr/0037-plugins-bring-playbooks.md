---
status: proposed
date: 2026-10-24
decision-makers: maintainer
related: 0021, 0028, 0032
---

# 0037: Plugins bring playbooks: asset types with discovery hints, template contracts and recommended settings

## Context and Problem Statement

The first contract worth validating is a team's weekly report: drafted by Friday 17:00 from the week's pull requests,
tracker items and the previous report, edited and sent by the person who owns it. The maintainer decided that the
core knows nothing about weekly reports: "Playbooks as plugins. A plugin can bring adapters, a playbook (asset types
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
  `*.prose.md` files (or directories of them) read at import. Everything is checked when declared: names are slugs,
  a hint names a capability's read method (never `agent-runner`), windows parse, patterns compile, setting keys are
  `<section>.<key>`, and every placeholder and `kept_by` names something the playbook declares.
- **Asset types and hints.** An `AssetType` has a name, a description, `DiscoveryHint`s and optionally `kept_by`, the
  template whose output keeps it true. A hint is a contract input's shape (`read`, `args`, `window`) plus `match`, a
  pattern on each returned item's `title`; a matching item's `ref`, `key`, `id` or `url` is the candidate's reference.
- **Templates.** A `ContractTemplate` is a contract's text with `{{asset:<type>}}` placeholders in double-quoted
  strings. `hz workflow new <name> --template <module>:<template> --asset <type>=<ref>` fills them, JSON-escaped, sets
  the name, and refuses a template whose result `hz workflow check` would reject.
- **Consumer.** The `workflow` module lists playbooks (`hz workflow playbooks`), runs hints (`hz workflow discover`,
  CLI and MCP only) through `Kernel.handle_for` as a read-only job of itself that declared just that capability, and
  provides the kernel's `PlaybookSetup` service. One failing hint is its own problem, with the command that fixes it.
- **Setup.** Unless `setup=False`, `module.playbook` declares the module's setup step: `hz <module> setup` and
  `hz setup` show the recommended settings and write them through `ConfigEditor` only on yes, offer the candidates
  for each asset type (or "fill it in later"), and write each template only when confirmed, under a name the person
  may change; an existing contract is left alone. A module with its own step passes `setup=False` and asks for
  `PlaybookSetup` in it.
- **Success measure.** Each render records its drafting time; `hz workflow sent <name> --file <f>` or `--ref <doc>`
  (the approved version where the asset lives, read through `documents`) records the share of words changed, both
  word counts and a hash, never the text; `hz workflow show` reports both per draft. It is about drafts, never people.

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

- `tests/unit/kernel/playbooks_test.py`: every check at declaration, filling placeholders, reading template files,
  the setup step `module.playbook` declares.
- `tests/unit/modules/workflow_playbooks_test.py`: discovery (matches, references, the candidate cap, a failing
  adapter, arguments that don't fit, an unbound capability), template lookup, a template that fails its checks, the
  setup walk writing only what is accepted.
- `tests/unit/modules/workflow_measure_test.py`: the diff ratio, records, `sent` and its refusals.
- `tests/integration/playbook_test.py`: a test plugin with adapters, a weekly report playbook and defaults, end to end
  through the CLI: playbooks, discover, setup answered on stdin, new from the template, a dry run that costs nothing,
  the daemon drafting on Friday 17:00 in a pinned time zone with lineage, and the success measure in `show`.

## More Information

- Decided by the maintainer, 2026-10-24: playbooks as plugins; a validation slice of one contract (draft ready
  Friday 17:00, real inputs through vendor CLIs, a local draft the person edits and sends, measured after three
  Fridays by time saved and how much was changed). The weekly report playbook itself lives in a reference plugin.
- Planned next: approval in place. The draft is written where the asset lives (this week's section of the rolling
  report document) with a draft tag; the person edits it and removes the tag to approve; hz reads the approver from
  revision history and diffs draft against approved. `kept_by` and `sent --ref` are the seams for it.
- Open: listing reads for `documents` (title patterns, folders) so hints can search; whether a playbook's defaults may
  name keys a project `hz.toml` can't set (today they go through `ConfigEditor`, user scope, on yes only).
