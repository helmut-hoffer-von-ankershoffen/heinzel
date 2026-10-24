---
status: proposed
date: 2026-10-24
decision-makers: maintainer
related: 0004, 0006, 0007, 0012, 0015, 0017, 0020, 0021, 0024, 0025, 0028, 0032, 0035, 0037
---

# 0040: Layers and playbooks are plugins: a plugin may be a layer with a parent and a site, and layers stack general to specific

## Context and Problem Statement

Anything specific to a company, site or team is a plugin (tenet 9), and a plugin already brings adapters
([ADR 0028](0028-integrations-are-capability-adapters.md)) and modules. What it cannot say today is *where it sits*: a
company has orgs, orgs have teams, and people work at sites that cut across that tree; a team wants its company's
adapters, its org's way of reporting, its site's time zone and its own template contracts, without copying any of
them. What hz should draft is now a plugin's playbook ([ADR 0037](0037-plugins-bring-playbooks.md)): asset types with
discovery hints, template contracts and recommended settings; but which plugin's playbook applies to a team, and in what
order when its company, org and site each bring one, is undecided, and a newcomer's first win ("perfect onboarding", a
separate record) is exactly such a draft. The maintainer decided (2026-10-24):
"Playbooks as plugins. Core knows nothing about weekly reports. A plugin can bring adapters, a playbook (asset types
with discovery hints and template contracts) and config defaults. Setup proposes assets found by those hints and offers
the template contracts." "Layers follow org and site, untyped. Each plugin is a layer with a parent: company → org →
team; a site under the company, orthogonal to the org tree. A team declares its org and site; hz stacks layers general
→ specific (company, org + site, team)." "Plugin names follow the path. Users always say 'plugin', never 'package'."
"Nothing company-specific in the public repo; a company's plugins live in its own private repo holding several
plugins." And a public reference company as the worked example. How does a plugin declare its place, what does the
stack mean, and where do playbooks live?

## Decision Drivers

- Tenet 9: the core is generic; a company's structure, tools and rituals are its plugins. Tenet 6: a team diverges from
  its org by adding a plugin, not by forking one.
- Perfect onboarding (tenet 11): installing one team plugin must bring everything the team's people need, offered in
  the right order, with the person's own configuration the only thing that is written
  ([ADR 0012](0012-layered-configuration.md), [ADR 0037](0037-plugins-bring-playbooks.md)).
- Declared beats discovered ([ADR 0028](0028-integrations-are-capability-adapters.md)): a layer's defaults are code the
  person installed, visible in `hz system info` with their origin, never something a repository pushed.
- One mechanism: plugins are already discovered, versioned, installed and named
  ([ADR 0004](0004-discovery-through-packages-and-entry-points.md), [ADR 0006](0006-plugins-installed-through-the-uv-tool-receipt.md),
  [ADR 0025](0025-plugin-distributions-are-named-heinzel-name.md)); a layer must not be a second kind of thing.
- A worked example that the public repository can test against without a single real company name.

## Considered Options

- **A plugin may declare itself a layer with a parent and a site; layers stack general to specific; playbooks are
  what a layer brings; names follow the path; a company's plugins live in its private repository; a public reference
  company is the example and the test bed** (chosen)
- **Typed layers** (`kind: company | org | team | site`, each with its own rules): more words for the same tree, and
  the first company that has a division between org and team, or two site levels, breaks the enum. Untyped layers with
  a parent model any depth; the words are labels hz derives from position.
- **Layers as Git repositories with a manifest** (the earlier onboarding draft): data, not code, so no adapters, no
  defaults the kernel can trust, and a repository someone else controls would steer a daemon
  ([ADR 0021](0021-workflow-contracts.md) §10). Repositories keep the data (roster, contracts, receipts); plugins carry
  the code and defaults.
- **One company plugin for everything**: no team can diverge without a company release; the org's playbook and the
  team's template contracts change at different speeds.
- **A configuration tree in the state repository** (`hz.toml` per level): the same remote-control problem, and no
  place for adapters or template contracts.
- **Layer defaults as a configuration floor below the files** (an earlier draft of this record): values a person
  never saw would steer bindings; [ADR 0037](0037-plugins-bring-playbooks.md) already decided that a playbook's settings
  are recommended and written only on acceptance, and a layer's defaults are its playbook's.
- **Playbooks in the core** (a weekly report module): the core would know one company's rituals, and every other
  company would configure them away. The core has a let to carry playbooks ([ADR 0037](0037-plugins-bring-playbooks.md))
  and nothing about their content.
- **Weekly reports as the first core feature, playbooks later**: the validation slice (INTENT.md, "Thin slice first")
  would then have to be unbuilt; building it as the reference company's playbook validates the mechanism and the
  slice at once.

## Decision Outcome

Chosen option (proposed): **a layer is a plugin that declares a parent and, for a team, a site; the kernel stacks the
installed layers general to specific and combines what each brings (adapters, playbooks with their recommended
settings, roles) in that order, offering the result and writing only what the person accepts; names follow the path;
a company's plugins live in its own private repository; a public reference company shows all of it.** Checkable
statements:

1. **The layer let.** `module.layer(parent="acme-payments", site="acme-lisbon")` on a plugin's module adds a
   `LayerLet` (one per module, like `InfoLet`): `parent` names the module of the layer above (none for the root),
   `site` optionally names a site layer. A site is a layer whose parent is the company (or an org) and that no org or
   team has as a parent; it is orthogonal to the org tree. Layers are untyped: the kernel checks that a named parent or
   site is an installed layer, that the tree has no cycle, and that `site` is not an ancestor; the words company, org,
   team and site are labels `hz org team show` and `hz system info` derive from position (root; inner node; a node with
   a roster; a node named as a site), not kinds the kernel enforces. A missing parent or site is a `degraded` doctor
   check naming the plugin to install; the plugin also lists its parent and site as package dependencies, so
   installing a team installs its stack ([ADR 0006](0006-plugins-installed-through-the-uv-tool-receipt.md)).
2. **Stacking order: general to specific.** For the layer a person is in (the plugin their team repository's `hz.yaml` names, `[team] layer` when
   they set it, or the only team layer installed otherwise; amended 2026-10-24), the stack is the root, then each org down the parent chain, then the site, then
   the team: company, org + site, team. A more specific layer refines a more general one; two layers that set the same
   key resolve by stack order. **Open: precedence when the org and the site conflict** (an org binding and a site
   binding for the same capability, an org default and a site default for the same setting). Proposal recorded for the
   maintainer: the team decides: a team layer that sets the key wins; where the team is silent, the conflict is a
   `degraded` doctor check that names both layers and the key, and the person's file decides until the team sets it.
   No silent merge. Implemented as the default behind one seam, the kernel's `ORG_SITE_PRECEDENCE`
   (`team_decides`, not public API), so the maintainer's decision changes one assignment (amended 2026-10-24).
3. **What a layer brings.** A layer is an ordinary plugin: its adapters are `ProviderLet`s, its roles extend ADR
   0024's vocabulary, its contracts are module contracts (namespace = module, [ADR 0021](0021-workflow-contracts.md)
   §10), and its configuration defaults are the recommended settings of its playbook
   ([ADR 0037](0037-plugins-bring-playbooks.md): capability bindings and the module's own settings, never what runs code
   or what it costs). Nothing a layer brings is applied silently: ADR 0012's precedence is unchanged, and a
   recommendation becomes configuration only when the person accepts it in setup or `join` and it is written to their
   user file, where `hz system info` shows it as theirs. A project `hz.toml` still cannot bind
   ([ADR 0028](0028-integrations-are-capability-adapters.md)).
4. **Stacked playbooks.** The playbooks of a stack ([ADR 0037](0037-plugins-bring-playbooks.md)) combine general to
   specific: asset types, hints and templates are a union, and an asset type or a recommended setting named by two
   layers is offered as the most specific layer's, with the general layer's shown as the alternative. Setup and `join`
   ([ADR 0032](0032-modules-declare-interactive-setup-steps.md), the onboarding record) propose the assets the stacked
   hints found and offer the template contracts and settings, most specific first; the person picks, and the copy lands
   in `me:` or `team:`. The core knows nothing about weekly reports: it knows asset types, hints and templates, and now
   the order of the layers that bring them.
5. **Names follow the path.** A layer's module name is its path from the root, joined with `-`: `acme`,
   `acme-payments`, `acme-payments-checkout`; a site hangs off its parent the same way: `acme-lisbon`. The parent's name
   plus `-` is a prefix of the child's, so the name alone says where a plugin sits; a layer that breaks this is a hint
   in doctor and `hz system layer list`, never refused (amended 2026-10-24 by the maintainer: a check, not enforced
   hard). Distributions are `heinzel-<name>`
   ([ADR 0025](0025-plugin-distributions-are-named-heinzel-name.md)): `heinzel-acme-payments-checkout`.
6. **The word is plugin.** Every user-facing text (CLI help, doctor, documentation, the setup and join screens) says
   "plugin", never "package" or "distribution"; the latter two appear only where the Python ecosystem forces them
   (`pyproject.toml`, `uv` output quoted verbatim). A test over the CLI help and the generated documentation keeps it so.
7. **Nothing company-specific in the public repository.** The core and its first-party plugins
   ([ADR 0017](0017-first-party-plugins-in-the-core-repository.md)) contain no layer of a real company. A company's
   plugins live in its own private repository holding several plugins, laid out like this repository's `plugins/`
   (one `uv` workspace, one distribution per layer, released there); `hz system plugin add` installs from it through a
   source the team manifest names (the onboarding record). `scripts/check_confidentiality.py` keeps real names out of
   the public repository, as it does today.
8. **The public reference company `heinzel-acme`.** A separate public repository, scaffolded from the template
   ([ADR 0007](0007-plugins-scaffolded-with-copier.md)), models a fictional company end to end: the company plugin `acme`
   (adapters bound by configuration defaults: code host and tracker through the vendor CLIs, documents and calendar),
   an org plugin `acme-payments` (a playbook with asset types and template contracts: the team's weekly report, board
   hygiene), a team plugin `acme-payments-checkout` (the team's own template contract and roles) and a site plugin
   `acme-lisbon` (time zone and working hours). It is the worked example in the documentation and the test bed: the
   core's e2e tests of layering, playbooks and onboarding install it and run the journeys against it, pinned to the
   core's version; a change to the layer let or the playbook let that breaks the reference breaks CI.

### Consequences

- Good: a team installs one plugin and has its company's adapters, its org's playbook, its site's defaults and its
  own templates, in an order anyone can read off the names; the person's file still wins over all of it.
- Good: the core stays ignorant of every company's rituals; the first ritual (the weekly report) is built where it
  belongs, in the reference company's playbook, and validates the let at the same time.
- Good: diverging is adding a more specific layer, never forking a general one; a company reorganisation is a rename
  along the path and a parent change, visible in a diff.
- Good: one public repository shows a company how to lay out its private one.
- Bad: a new let (`LayerLet`) and the stacking of playbooks on top of [ADR 0037](0037-plugins-bring-playbooks.md);
  conflicts between org and site need a decision and, until then, a doctor finding.
- Bad: names by path are long (`heinzel-acme-payments-checkout`), and a move in the org tree is a rename of the
  distribution; `hz org team show` and the labels hide the length from people.
- Bad: "site" now means a location layer and, in [ADR 0012](0012-layered-configuration.md), the machine-wide
  configuration file; documentation says "site layer" and "site file" and never a bare "site" where both could be meant.
- Bad: the reference repository is one more thing to release in step with the core.

### Confirmation

In part (the mechanism in the core, 2026-10-24): `LayerLet` declaration checks when a module is imported (one per
module, no self-parent or self-site, a site that isn't the parent; the path prefix is a hint) and when the kernel
registers layers (installed parent and site, no cycle, one parent per layer, a plugin's top-level module, a site
outside the layer's own line), the stack order
for a team with an org chain and a site, the conflict finding and the combination of stacked recommended settings
(`tests/unit/kernel/layers_test.py`); `hz system layer list` and `show`, `hz system info`, doctor naming a missing
parent and an org–site conflict, and `hz setup` offering each setting with the layer that wins it, most specific first
(`tests/integration/layer_test.py`, with made-up Acme layers in `tests/fixtures/layers`). Still to come: roles from the stack validated in the roster (today `hz system layer show` lists them); the playbook let's own tests (its record); the word
"plugin" in CLI help and generated documentation (`tests/unit/documentation_test.py`); the reference company's
plugins installed and the onboarding journeys run against them (`tests/e2e/reference_company_test.py`), with the
reference repository's own CI running the core's `make check` on every release; the confidentiality check on the public
repository, unchanged.

## More Information

- Decided by the maintainer, 2026-10-24: playbooks as plugins with the core knowing nothing about weekly reports;
  untyped layers following org and site with general-to-specific stacking; names by path and the word "plugin"; a
  company's plugins in its private repository; the public reference company. Technical shape by the architect; the
  org–site precedence is open with the proposal above.
- Builds on [ADR 0037](0037-plugins-bring-playbooks.md) (playbooks and recommended settings; this record adds the
  stack that combines them) and [ADR 0012](0012-layered-configuration.md) (unchanged: nothing a layer brings is
  configuration until accepted), and on [ADR 0004](0004-discovery-through-packages-and-entry-points.md) and [ADR 0006](0006-plugins-installed-through-the-uv-tool-receipt.md)
  (a layer is discovered and installed like any plugin, and depends on its parents),
  [ADR 0007](0007-plugins-scaffolded-with-copier.md) (the reference company and a team's plugin are scaffolded from the
  template), [ADR 0015](0015-health-checks-as-lets.md) (doctor), [ADR 0017](0017-first-party-plugins-in-the-core-repository.md)
  (the layout a private company repository copies), [ADR 0020](0020-daemon-head.md) and [ADR 0021](0021-workflow-contracts.md)
  (the team repository stays the state repository; template contracts are contracts),
  [ADR 0024](0024-people-and-org.md) (roles), [ADR 0025](0025-plugin-distributions-are-named-heinzel-name.md) (names),
  [ADR 0028](0028-integrations-are-capability-adapters.md) (adapters and bindings) and
  [ADR 0032](0032-modules-declare-interactive-setup-steps.md) (setup offers what playbooks found).
- Proposed with two other records, referenced by name until merged: "perfect onboarding" (the team repository names
  its team plugin; `join` installs the stack on one yes and takes the first win from the stacked playbooks) and
  [ADR 0035](0035-integrations-go-through-vendor-clis-as-an-ordinary-employee.md) (the adapters the company layer
  brings use the vendor CLIs; a company's private layer may bind other providers). The playbook let is
  [ADR 0037](0037-plugins-bring-playbooks.md)'s; this record decides which layers' playbooks apply and how a stack
  combines them, not the let's API.
- Open: org–site precedence (proposal: the team decides); whether a person may be in two teams (two stacks) and how
  `hz system info` shows that.
