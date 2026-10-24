---
status: proposed
date: 2026-10-24
decision-makers: maintainer (product), architect (technical shape)
amends: 0018, 0024
related: 0006, 0012, 0014, 0017, 0020, 0021, 0025, 0028, 0030, 0032, 0035, 0037, 0040
---

# 0041: Perfect onboarding: one invite line joins a team, identity is discovered, and joining ends with a first win

## Context and Problem Statement

Everyone on a team onboards the same way, every role included, and each person runs one personal daemon
([ADR 0020](0020-daemon-head.md)). Today a new person installs `hz`, reads [CONFIGURATION.md](../CONFIGURATION.md),
binds capabilities, stores credentials, installs plugins, clones a team repository and points
`[workflow] team_repository` at it; nothing tells them who they are to hz, which team they belong to, or what to run
first. `hz setup` exists as a per-module wizard ([ADR 0032](0032-modules-declare-interactive-setup-steps.md)) and knows
nothing about people or teams. [ADR 0024](0024-people-and-org.md) says where people and teams come from, but not how a
person joins. The maintainer decided (2026-10-24): "Perfect onboarding is a principle. Joining a team: one
non-personalized invite line (the team repo), e.g. `uvx heinzel join <team repo>`, posted in chat. Access is controlled
by repo permissions. hz auto-discovers who the person is from their GitHub/Google login and matches the team roster;
asks once only if no match. hz drafts the roster (org chart / Jira team) for the manager to approve. Join ends with a
first win (e.g. your part of this week's report). Works from a terminal or from Claude Code alike." In the same
decisions, a team, its org, its company and its site are plugins that stack into layers
([ADR 0040](0040-layers-and-playbooks-are-plugins.md)), and what hz can draft comes from the playbooks those plugins
bring ([ADR 0037](0037-plugins-bring-playbooks.md)). What is the model, and
what does `join` do?

## Decision Drivers

- Perfect onboarding is a tenet (INTENT.md, tenet 11): the first ten minutes decide adoption, and a join that needs a
  form, a ticket or an administrator is not perfect.
- Tenets 2, 6 and 9: for all roles; teams extend without forking; anything company-specific is a plugin. The core
  knows nothing about weekly reports: what a newcomer gets drafted comes from the team's layers and their playbooks.
- No server of our own ([ADR 0020](0020-daemon-head.md)): Git repositories are the shared state, and the code host's
  permissions are the only access control we have.
- A repository someone else controls must not decide where a person's data goes or what their daemon runs
  ([ADR 0021](0021-workflow-contracts.md) §10, [ADR 0028](0028-integrations-are-capability-adapters.md)); a plugin the
  person chose to install is trusted code ([ADR 0020](0020-daemon-head.md)) and may bring defaults.
- Minimised personal data in Git history ([ADR 0024](0024-people-and-org.md) §6); nothing measures individuals.
- Agents onboard people too ([ADR 0014](0014-agent-skills-and-the-claude-code-plugin.md)): what a person types in a
  terminal, a coding agent runs for them, with the same one line.
- Identity already exists: the person is logged in to the code host and the office suite through the vendors' own
  CLIs, which hz uses as its transports ([ADR 0035](0035-integrations-go-through-vendor-clis-as-an-ordinary-employee.md)).

## Considered Options

- **One invite line; the team repository is the invite; identity discovered from the person's logins and matched to
  a manager-approved roster; the team's plugin and its layers installed once; join ends with a first win** (chosen)
- **A personalised invite (token, link or e-mail per person):** tokens need a server to mint and check them, against
  ADR 0020, and someone must create one per person. The repository's permissions already say who may join; a line
  that is the same for everyone can be posted once in the team's channel.
- **Joining as a pull request the member opens** (the earlier draft of this record): a pull request per newcomer, an
  owner's review before anything works, and a form to fill where the roster already knows the person. Kept only as
  the fallback when the roster has no match: then hz drafts the roster change for the manager.
- **Asking the person who they are** (handle, role, team, manager): questions whose answers hz can read from the
  code host login, the office-suite login and the roster. Asked once, and only when no match is found.
- **A hand-kept roster** in the team repository: drifts from the org chart and the tracker's team, and someone has to
  type it. hz drafts it from those sources; the manager approves and owns it ([ADR 0024](0024-people-and-org.md)).
- **The roster inside the team plugin**: a plugin release for every joiner and leaver, and a manager approving code.
  Code and defaults are a plugin; data that changes weekly is the repository.
- **A manifest that recommends plugins, bindings and contracts** (the earlier draft): once a team is a plugin, its
  defaults travel with the plugin the person installs, and the manifest only has to name it. Recommendations as a
  separate mechanism are dropped.
- **One `hz setup` journey in five phases for person, team, org and company** (the earlier draft): the right shape for
  a company rollout, but it front-loads structure a newcomer does not need on day one. Org, company and site are
  layers the team plugin depends on; the newcomer sees them once, as the plugins `join` installs. `hz setup` stays the
  per-module wizard of ADR 0032 and `join` calls it for what is missing.
- **A manifest that applies configuration remotely** (bindings, scheduled contracts on every fetch): it hands control
  of a daemon to whoever can push to a repository; ADRs 0021 and 0028 refuse this for project files, and a team
  repository is no different in kind. A plugin is installed once, explicitly, and upgraded explicitly.

## Decision Outcome

Chosen option (proposed): **joining a team is one non-personalised line posted in chat; the team repository is the
invite and its permissions are the access control; hz discovers who the person is from their existing logins and
matches them to a roster the manager approved; `join` installs the team's plugin and the layers it depends on, once;
join ends with a first win from the team's playbooks; the same line works in a terminal and through a coding agent.**
Checkable statements:

1. **The invite is one line, the same for everyone**: `uvx heinzel join <team repository>`, posted once in the team's
   channel. For this the `heinzel` distribution gains a `heinzel` console script beside `hz` (the same entry point), so
   `uvx` runs it without an install. `join` installs `hz` as a uv tool
   ([ADR 0006](0006-plugins-installed-through-the-uv-tool-receipt.md)) and continues as `hz join <team repository>`,
   which is what a person with `hz` installed types. Whoever can read the repository can join; whoever cannot gets the
   code host's answer and the name of the team's owner from the manifest, nothing more. There are no invite tokens and
   no server.
2. **The team repository is data; the team plugin is code.** The repository the line names is the team's state
   repository ([ADR 0020](0020-daemon-head.md)): the roster, the people files, team contracts, policies and receipts,
   and a manifest `hz.yaml` with the team's `name`, its owner roles and `plugin:` (the team plugin's distribution,
   `heinzel-acme-payments-checkout`, and where to install it from: the company's private plugin repository, an index, or
   a `plugin/` directory of this repository for a team on its own). The plugin is a layer with a parent and a site
   ([ADR 0040](0040-layers-and-playbooks-are-plugins.md)): it carries adapters, configuration defaults, the team's
   playbook and template contracts, and depends on its org, company and site plugins (`heinzel-acme-payments`, `heinzel-acme`,
   `heinzel-acme-lisbon`), so installing it installs the stack. Code changes by release, data by pull request; a roster
   change never needs a plugin release.
3. **Identity is discovered, not asked.** `join` reads who the person is from the logins they already have: the code
   host CLI (`gh api user`: login, name) and, when bound, the office suite CLI (the account's e-mail). It matches the
   roster by login or e-mail. It asks nothing when it finds exactly one match. It asks once, one question with the
   candidates, when the match is ambiguous or missing, and records the answer as a proposed roster change for the
   manager (5), never as a fact. hz never asks for a name, an e-mail, a password or a token.
4. **The roster is the team.** `team/roster.yaml` holds, for each member, a handle (the code host login by default),
   the code host login, the office-suite e-mail, the hz roles and the membership dates, within what
   [ADR 0024](0024-people-and-org.md) §6 allows; name, title, manager and location stay out of Git. Roles are hz
   vocabulary (`engineer`, `principal`, `engineering-manager`, `product-manager`, `program-manager`, `*-leadership`),
   extended by a layer's plugin. Data files people write are YAML, configuration stays TOML (the recommendation of ADR
   0021; this settles ADR 0024's open question 1 and amends its "declared files use TOML").
5. **hz drafts the roster; the manager approves and owns it.** `hz org team roster draft` reads the sources that already
   know the team: the tracker's team and the code host's team through the bound adapters, and, when the company plugin
   brings one, the directory of record ([ADR 0024](0024-people-and-org.md) §2). It writes the draft as a pull request
   against the team repository; CODEOWNERS of `team/` (the owner roles, by default `engineering-manager` and
   `*-leadership`) merge it, and the creator of a repository is its first owner. A newcomer the roster lacks is
   proposed the same way. Removal sets `until`; `hz org privacy forget` (ADR 0024) does the rest.
6. **Install once, explicitly.** After identity and the team, `join` shows the plugins the manifest names and their
   layers, where each comes from and what each brings (adapters, bindings and defaults, playbook, template contracts),
   and installs them on one yes ([ADR 0006](0006-plugins-installed-through-the-uv-tool-receipt.md)); `--yes` answers it.
   Then it runs the module setup steps the person still lacks (ADR 0032's `SetupLet`s, for the adapters the layers
   bring: a vendor's login, nothing else). The layers' playbooks recommend bindings and settings
   ([ADR 0037](0037-plugins-bring-playbooks.md)); `join` offers them, most specific layer first, and writes what the
   person accepts to their user configuration ([ADR 0012](0012-layered-configuration.md) is unchanged: nothing applies
   silently, and `hz system info` shows every effective value as the person's). No file in the team repository binds a
   capability, stores a credential or schedules a contract on a daemon; upgrades of the plugins are explicit
   (`hz system plugin`), never a side effect of the daemon's fetch; team contracts still need
   `[workflow] schedule_team` and may be pinned ([ADR 0021](0021-workflow-contracts.md) §10). Manifest and roster are
   untrusted text, parsed with ADR 0021's strict YAML loader.
7. **Join ends with a first win.** Before it says anything else, `join` does one thing for the person: the first
   template contract the stacked playbooks name for the person's role, once, by hand, within the cost cap it shows
   first. The core ships no such contract and knows no weekly report; the playbook does
   ([ADR 0037](0037-plugins-bring-playbooks.md)), and the reference company's
   is "your part of this week's team report" (what you merged and moved, as a local draft you may edit or discard). A
   playbook may also propose the assets its discovery hints found (the team's report document, its board) and the
   template contracts that maintain them; `join` offers, the person picks. Nothing is published or scheduled, and no
   daemon is installed by the first win; `join` ends with doctor and "what happens next" (schedule it, install the
   daemon, invite the next person: the same line).
8. **Terminal and coding agent alike.** `join` is a CLI and MCP operation with `--json`, every question has a flag
   (`--role`, `--as <handle>`, `--yes`), and `--non-interactive` fails naming the question instead of asking. The
   Claude Code skill `hz-init` runs the same line a person would, relays the one question if it comes, and relays the
   first win. No head or agent gets a shorter or longer journey than a terminal.
9. **Re-runnable.** `join` on a joined team re-reads the roster, offers the plugins not yet installed and the win again;
   nothing is asked twice. `[org] team` holds the repository URL and replaces `[workflow] team_repository`, which is
   deprecated; the checkout lives under the data directory and is fetched by the daemon's tick.
10. **Privacy by construction.** What goes to a repository is the roster (4) and the person's own
    `people/<handle>.yaml` (time zone, working hours, opt-outs; owned through CODEOWNERS). `join` says "what hz keeps
    about you, and where" before writing anything; no step computes or stores anything about a person's activity; the
    first win is the person's own draft of their own work ([ADR 0024](0024-people-and-org.md) §7).
11. **Namespace.** `hz join` is a top-level command, decided by the maintainer as the invite line, and the second
    exception to [ADR 0018](0018-one-administrative-namespace.md) after `hz setup`. Roster and team operations live in
    the `org` module ([ADR 0024](0024-people-and-org.md)): `hz org team new` (the state repository and, for a team on its
    own, a team plugin from the template), `hz org team roster draft|show`, `hz org team show` (the stack of layers the
    person is in), `hz org person me`.

### Consequences

- Good: a newcomer joins with a line they copy from chat, says yes twice, and gets a draft of their own week before
  they have read a page of documentation; a manager approves one roster pull request instead of one membership pull
  request per person; access control is the code host's, not ours.
- Good: the company's specifics (adapters, defaults, playbooks) arrive as the plugins the team depends on, installed
  once; the core stays generic and the public repository holds nothing company-specific.
- Good: the same line serves a terminal and a coding agent; the `hz-init` skill shrinks to running it.
- Good: a team of one needs a Git repository and the template's team plugin inside it.
- Bad: the roster's sources need the vendor CLIs logged in on the manager's machine; a team without any source types
  its roster once, and hz still drafts the newcomer entries.
- Bad: a person without a code host login cannot be matched and cannot own or approve
  ([ADR 0024](0024-people-and-org.md) §4); they can still be in the roster and receive drafts.
- Bad: the plugins are the one thing `join` installs from a repository the person did not pick; the stack is shown
  with its origins before the yes, and the private company repository is the trust decision
  ([SECURITY.md](../../SECURITY.md)).
- Bad: the `heinzel` console script is a second name for the same command; it exists for `uvx` only and is not
  documented as an alias.
- Bad: new work: the `org` module (roster model, `roster draft`, `OrgContext`, `person me`), `hz join` with its
  identity discovery, the one-yes install of the stack and the first-win hook into playbooks, the `heinzel` console
  script, the team repository template (manifest, CODEOWNERS, roster, a starter team plugin, the Actions runner),
  doctor checks for a stale checkout, tests of the journeys against a local bare repository and the public reference
  company, and documentation.

### Confirmation

Not yet: proposed. When implemented: unit tests of the roster and manifest models, identity matching (exactly one
match asks nothing; none or several ask once and propose, never record) and the first-win selection from stacked
playbooks per role (`tests/unit/modules/org_*_test.py`, `tests/unit/heads/cli_join_test.py`); integration tests that
run the journeys of the research note with scripted answers, fake vendor CLIs and the reference company's plugins
against a local bare Git repository standing in for the code host (`tests/integration/onboarding_test.py`), asserting
that no name, title or secret reaches a repository file, that a hostile manifest cannot bind, store or schedule
anything and installs nothing without the yes, and that `--non-interactive --json` answers exactly what the terminal
does; `tests/unit/public_api_test.py` for the `heinzel` console script; the e2e test of the Claude Code skill running
the invite line.

## More Information

- The journeys, screen transcripts, the model, the questions answered and the build plan:
  [docs/research/onboarding.md](../research/onboarding.md).
- Decided by the maintainer, 2026-10-24: the principle, the single invite line, identity from logins matched to a
  roster hz drafts and the manager approves, the first win, terminal and coding-agent parity; layers and playbooks as
  plugins, named by path, a company's plugins in its private repository, a public reference company. The earlier draft
  of this record (one `hz setup` journey in five phases, membership by pull request, manifest recommendations, org and
  company layers as repositories) is superseded by this text before acceptance.
- Amends [ADR 0018](0018-one-administrative-namespace.md): `hz join` is a top-level command. Amends
  [ADR 0024](0024-people-and-org.md): the roster is drafted by hz from the tracker's team or a directory and approved
  by the manager, a person is matched by login or e-mail from the vendor CLIs' own logins, data files are YAML, and
  self-declaration is the fallback, not the first source. Builds on [ADR 0020](0020-daemon-head.md) (state
  repository), [ADR 0021](0021-workflow-contracts.md) (namespaces, pinning, the first win is a contract run by hand),
  [ADR 0017](0017-first-party-plugins-in-the-core-repository.md) and
  [ADR 0025](0025-plugin-distributions-are-named-heinzel-name.md) (the team plugin is a plugin like any other, named
  `heinzel-<path>`), [ADR 0028](0028-integrations-are-capability-adapters.md) (bindings are the person's, with the
  layers' defaults below them; the roster's sources are capability reads),
  [ADR 0030](0030-credentials-live-in-the-os-keychain.md) (nothing in a repository is a secret) and
  [ADR 0032](0032-modules-declare-interactive-setup-steps.md) (`join` runs the setup steps the layers' adapters need;
  the steps are unchanged).
- Builds on [ADR 0035](0035-integrations-go-through-vendor-clis-as-an-ordinary-employee.md) (the logins `join`
  discovers identity from; until it is accepted, `join` reads the code host login through the bound code host adapter)
  and [ADR 0037](0037-plugins-bring-playbooks.md) (playbooks, template contracts and recommended settings), and on
  [ADR 0040](0040-layers-and-playbooks-are-plugins.md) (the layer let with its parent and site, the stacking order, naming
  by path, the private company repository and the public reference company).
- Open: the first win per role beyond what the reference company's playbook defines; whether a team without a tracker
  team or directory gets a roster wizard or a template file; how `join` behaves when the stack conflicts with plugins
  the person already has (the layer record's open precedence question).
