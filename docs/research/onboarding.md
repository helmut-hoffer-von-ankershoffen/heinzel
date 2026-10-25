# Research: perfect onboarding, one invite line

> Historical design research and illustrative transcripts, not current installation instructions.
> [ADR0080](../adr/0080-one-invite-line-and-a-reviewed-first-win.md) reconciles these journeys with trusted identity,
> approvals, persistent installation and explicit background consent. The README describes what is available today.

- Date: 2026-10-24 (revised the same day to the maintainer's decisions)
- Scope: the journeys, the model, the decisions and the build plan behind
  [ADR 0080](../adr/0080-one-invite-line-and-a-reviewed-first-win.md) (proposed). Builds on ADR 0006 (uv tool
  receipt), 0020 (state repository), 0021 (contracts, namespaces, pinning), 0024 (people and teams), 0025 (plugin
  names), 0028 (bindings), 0030 (credentials) and 0032 (setup steps), and on two records proposed with it: "layers and
  playbooks are plugins" and "integrations go through the vendor's CLI as an ordinary employee".
- Every name here is made up and matches the public reference company: Acme (company), Payments (an org), Checkout (a
  team in Payments), Lisbon (a site); the people Mira (engineering manager), Theo (engineer) and Priya (product
  manager).

## TL;DR

1. **One line, posted once.** `uvx heinzel join git@github.com:acme-example/hz-team-checkout.git` goes into the team's
   channel. It is the same for everyone; whoever can read the repository can join. No tokens, no server, no form.
2. **hz knows who you are.** It reads your code host login (`gh`) and, when bound, your office-suite account (`gws`), and
   finds you in the team's roster. It asks one question only when it can't, and turns your answer into a proposal for
   the manager, not a fact.
3. **The manager owns the roster, hz drafts it.** `hz team new --roster github:<org>/<team>` reads the code host's
   team (later the tracker's team or the directory) for one pull request; the manager merges. Newcomers the roster
   lacks are proposed the same way.
4. **The team is a plugin; the repository is its data.** The invite repository holds the roster, contracts and receipts
   and names the team plugin (`heinzel-acme-payments-checkout`), which depends on its org, company and site plugins.
   `join` shows the stack and installs it on one yes; its adapters, defaults and playbooks come along.
5. **Join ends with a win.** Before it says anything else, `join` runs the first template contract the playbooks name
   for your role, locally, within a cap it shows first. For Acme that is your part of this week's team report. Then
   doctor and "what happens next".
6. **Terminal and coding agent alike.** The same line, every question a flag, `--json`; the Claude Code skill runs it
   and relays the one question if it comes.

## 1. The journeys

### 1.1 (a) An engineer joining

Theo copies the line from the channel. He has `gh` and `gws` logged in; he has never installed hz.

```text
$ uvx heinzel join git@github.com:acme-example/hz-team-checkout.git

🧝 Heinzelmännchen · join

Installing hz as a uv tool... done (hz 0.2.0). From now on: hz join, hz ...

What hz keeps about you, and where
  On this machine      your configuration, drafts, receipts; logins stay in gh and gws
  In the team's repo   the roster the manager approved (login, e-mail, role, dates); a preferences file only you edit
  Nowhere              your name, title, manager, location, or anything about how much you do
  Details: hz org privacy notice

Team checkout (hz-team-checkout): you can read it.
You are theo-example on github.com and theo@acme.example in Google Workspace.
Roster: theo-example, engineer, since 2026-10-01. Welcome, Theo.

This team is the plugin acme-payments-checkout, which brings its layers:
  acme                   company   adapters github (gh), google (gws); bindings code-host, tracker, documents, calendar
  acme-payments          org       playbook "payments": weekly team report, board hygiene; roles
  acme-lisbon            site      defaults: time zone Europe/Lisbon, working hours 09-18
  acme-payments-checkout team      template contract my-week, the team's report document
  from git+ssh://git@github.com/acme-example/heinzel-acme-plugins.git (private)
Install these four plugins? Their recommended bindings are offered next; only what you accept is written. [Y/n]: y
  Installed. gh: logged in as theo-example.   gws: logged in as theo@acme.example.   Nothing to store.

Your first win: your part of this week's team report, from what you merged and moved since Monday.
Reads github pull requests and issues of the last 5 days through gh; spends at most 1.00 USD. Run it now? [Y/n]: y
  Rendered in 31 s (0.17 USD). Draft: ~/.local/share/hz/drafts/my-week/2026-10-24.md
  It is yours: edit it, paste it into the team report, or discard it. Nothing was published.

── doctor ──────────────────────────────────────────────────────────────────
  ✓ team                 checkout (hz-team-checkout, main); layers acme › payments + lisbon › checkout
  ✓ code-host:read       github via gh 2.80 (theo-example)
  ✓ tracker:read         github via gh
  ✓ documents:read       google via gws (theo@acme.example)
  ! daemon               not installed (fine; nothing runs in the background until you say so)

What happens next
  Every Friday 16:00   hz workflow schedule me:my-week      drafts your part again, for you to edit
  Background           hz system daemon install             runs scheduled contracts while you work
  Teammates            post the same line in the channel; it is the invite
```

Theo typed one line and answered three questions, all "y". The win existed before he read any documentation.

### 1.2 (b) The manager creating the team and drafting the roster

Mira, the engineering manager, is first. Acme's plugins already exist in the company's private plugin repository
(company, the Payments org, the Lisbon site); she adds the team.

```text
$ hz team new checkout --github acme-example/hz-team-checkout --roster github:acme-example/checkout \
    --plugin acme-payments-checkout --parent acme-payments --site acme-lisbon
  Created hz-team-checkout from the template: hz.yaml (plugin: acme-payments-checkout), CODEOWNERS,
  team/roster.yaml (you, engineering-manager, owner), people/mira-example.yaml, workflows/,
  .github/workflows/hz-team.yml. Pushed main.
  Scaffolded the team plugin heinzel-acme-payments-checkout (layer: parent acme-payments, site acme-lisbon; an empty
  playbook) in ./heinzel-acme-payments-checkout; move it into acme-example/heinzel-acme-plugins and release it, or
  keep it in this repository under plugin/.

  Roster draft:
  Sources: github team acme-example/checkout (gh), atlassian team "Checkout" (acli).
  7 people in both, 1 only in the tracker (priya-example: product-manager?), 0 only on github.
  Roles from the tracker's team roles where they map, engineer otherwise; edit the pull request.
  Pull request #1 in hz-team-checkout: team/roster.yaml (8 members). Merge it to approve.

$ hz join git@github.com:acme-example/hz-team-checkout.git
  You are mira-example (github) and mira@acme.example (gws). Roster: engineering-manager, owner.
  Your first win: the team's weekly report draft from this week's merged pull requests and moved issues...
```

The roster is the manager's: hz read it from the tools that already know the team, she reviews and merges. Each later
roster draft is a diff (joined, left, changed role), never a rewrite. The plugin changes by release, the
roster by pull request; a joiner never needs a release.

### 1.3 (c) No match

Priya is a product manager on the tracker's team but not in the roster yet (Mira merged a smaller draft).

```text
$ uvx heinzel join git@github.com:acme-example/hz-team-checkout.git
  You are priya-example on github.com. The roster has no entry for priya-example or priya@acme.example.
  Join as 1) engineer  2) product-manager  3) program-manager  4) someone else is me (enter the handle) [2]: 2
  Proposed: pull request #5 adds priya-example, product-manager, to team/roster.yaml. Mira (owner) approves.
  Until it merges you read the team's contracts; your first win runs now anyway.
```

One question, once. The answer becomes a proposal the manager approves; hz does not add anyone to a roster on their
own say-so.

### 1.4 (d) From Claude Code

Theo pastes the same line into Claude Code. The `hz-init` skill runs it:

```text
$ hz join git@github.com:acme-example/hz-team-checkout.git --json --non-interactive
```

Every question is a flag (`--role`, `--as <handle>`, `--yes` for the install and the win); `--non-interactive` fails
naming the question rather than asking (`join.role: no roster match for theo-example; pass --role`), and the skill
relays exactly that question to the person, then runs the line again with the answer. The output is the same JSON a
terminal's `--json` prints: who was matched, which plugins were installed, the draft's path, doctor. No head gets a
shorter or longer journey.

### 1.5 Re-running

`hz join` on a team already joined re-reads the roster, offers the plugins not yet installed and the win again, and
asks nothing it already knows. `hz setup` (ADR 0032) stays the per-module wizard; `join` calls the steps the layers'
adapters need (a vendor login) and nothing else.

## 2. The model

### 2.1 Entities

| Entity | What it is | Lives in | Identity |
| --- | --- | --- | --- |
| Person | one human, one daemon | user configuration `[org]`; the roster; `people/<handle>.yaml` | handle (the code host login by default), code host login, office-suite e-mail |
| Team | the unit of shared state: roster, contracts, approvals, receipts | a Git repository with `hz.yaml` naming the team plugin | repository URL; `name` |
| Layer | a plugin with a parent and, for a team, a site: company, org, team, site (untyped) | the company's private plugin repository (or `plugin/` in the team repository) | distribution name by path, `heinzel-acme-payments-checkout` |
| Role | hz vocabulary, not job titles; a person may hold several | core list, extended by a layer plugin | id |
| Membership | person, roles, from, until | `team/roster.yaml` | — |

The layer tree, stacking order, playbooks and naming are the subject of
[ADR 0040](../adr/0040-layers-and-playbooks-are-plugins.md); onboarding only needs that a team plugin pulls in its stack.

### 2.2 A team repository

```text
hz-team-checkout/
  hz.yaml                      name, owners (roles), plugin: (distribution and source), roster sources
  CODEOWNERS                   team/ → owner roles; people/<handle>.yaml → that person
  team/roster.yaml             handle, login, e-mail, roles, from, until (ADR 0024 §6); drafted by hz, merged by owners
  people/<handle>.yaml         time zone, working hours, preferences, opt-outs; owned by the person
  workflows/*.prose.md         team contracts (ADR 0021); the template contracts the playbook offers land here as copies
  policies/, control.toml      auto-approvals, kill switch, cost ceilings (ADR 0020)
  receipts/, cursors/          written by daemons and the Actions runner (ADR 0020)
  plugin/                      optional: the team plugin itself, for a team without a company plugin repository
  .github/workflows/hz-team.yml  the Actions team runner, hz workflow check
```

```yaml
# hz.yaml
name: checkout
owners: [engineering-manager]                 # roles that merge roster changes
plugin:
  name: acme-payments-checkout                # heinzel-acme-payments-checkout; its layers come as dependencies
  source: git+ssh://git@github.com/acme-example/heinzel-acme-plugins.git#subdirectory=heinzel-acme-payments-checkout
roster:
  sources: [tracker-team: "Checkout", code-host-team: "acme-example/checkout"]
```

```yaml
# team/roster.yaml
- handle: mira-example
  login: mira-example
  email: mira@acme.example
  roles: [engineering-manager]
  from: 2026-10-24
- handle: theo-example
  login: theo-example
  email: theo@acme.example
  roles: [engineer]
  from: 2026-10-01
```

What is code and what is data: adapters, bindings and configuration defaults, the playbook (asset types with discovery
hints, template contracts) and role additions are the plugin, installed and upgraded explicitly, released like any
plugin. The roster, the people files, the team's own contracts, policies and receipts are the repository, changed by pull
request and fetched by the daemon's tick. The line between them is "does it change when a person joins or a document
moves" (data) or "when the team decides to work differently" (code).

### 2.3 Identity and trust

- **Logins are the identity.** `gh api user` gives the code host login; `gws` gives the account's e-mail. Both CLIs own
  their authentication; hz stores no token and never asks for one ("integrations go through the vendor's CLI").
  Matching is exact on login or e-mail, never fuzzy on names.
- **The roster is the truth, the manager owns it.** CODEOWNERS of `team/` are the owner roles; the creator of a
  repository is its first owner. hz drafts, humans merge. Anyone may propose (a newcomer without a match, a manager's
  `roster draft`); only owners approve.
- **The stack is the trust decision.** `join` installs plugins from the source the manifest names, once, after showing
  them; the company's private plugin repository is where a company keeps what it trusts
  ([SECURITY.md](../../SECURITY.md)). Upgrades are explicit.
- **Trust ladder.** Reading (clone) → in the roster (merged) → owner (merges roster, policies, pins) → the company's
  plugin repository (releases the layers). Each rung is a code host permission or a CODEOWNERS entry; hz checks, the
  code host enforces.
- **Directory of record.** When the company plugin brings an IdP source, the roster's `sources` names it and the IdP
  decides existence and manager (ADR 0024 §2); the tracker's team is the source for teams without one.

### 2.4 Recommendations from plugins and from the team repository, applied only on acceptance

ADR 0012's precedence is unchanged. For code, a layer's defaults are its playbook's recommended settings (ADR 0037:
bindings and the module's own settings); `join` offers them general to specific (company, then org and site, then
team, the most specific as the default). For team data, the manifest's `recommend:` (plugins, bindings, credential
names, contracts) is shown by `hz org sync` beside what the person has. Either way only what the person accepts is
written, to their user file. A repository still steers nothing: the manifest names the plugin and recommends, and only
the person's yes installs or applies; nothing in the repository binds, stores or
schedules (ADR 0021 §10, ADR 0028), because a team repository is writable by everyone on the team and fetched by the
tick.

### 2.5 Privacy

| Data | Where | Why |
| --- | --- | --- |
| handle, login, e-mail, roles, memberships with dates | `team/roster.yaml` | matching, CODEOWNERS, selectors (ADR 0024 §6) |
| time zone, working hours, preferences, opt-outs | `people/<handle>.yaml`, owned by the person | self-sovereign; edits are a pull request |
| name, title, manager, location | nowhere in Git; read at run time from the directory when one exists | ADR 0024 §6 |
| what you did, how much, how fast | nowhere | ADR 0024 §7; the first win is your draft of your own work |

`join`'s first screen says this before anything is written. It is information, not consent (consent is weak in
employment); `hz org privacy notice` is the long form.

## 3. Decisions (maintainer, 2026-10-24) and what they settle

| Decision | Settles |
| --- | --- |
| Perfect onboarding is a principle | INTENT.md tenet 11; every onboarding question is decided for the newcomer's first ten minutes |
| One non-personalised invite line, the team repository, posted in chat | the invite is the URL (earlier question 2); no tokens, no server; `heinzel` console script for `uvx` |
| Access is controlled by repository permissions | who may join (earlier question 7): reading the repository is enough; owners approve the roster, not each member |
| hz discovers identity from the GitHub/Google login and matches the roster; asks once only if no match | no identity questions (earlier phase `identity`); handle defaults to the login (earlier question 10) |
| hz drafts the roster (org chart, tracker team) for the manager to approve | the roster replaces member-opened join pull requests; self-declaration is the fallback (earlier question 14) |
| Join ends with a first win | the finale asks once, shows the cap, runs the playbook's first contract for the role (earlier question 8); daemon install is offered, not done (earlier question 11) |
| Terminal and Claude Code alike | every question a flag, `--json`, `--non-interactive`; the `hz-init` skill runs the line (earlier answers file folded into flags) |
| Layers and playbooks are plugins; a company's plugins in its private repository; names follow the path | the team is a plugin the invite repository names; org, company and site arrive as its dependencies (earlier questions 1, 3, 4, 12); manifest recommendations stay for team data beside plugin defaults for code, both recommend-only (earlier question 9); the core ships no report contract |

Earlier recommendations kept because the decisions do not touch them: a core role vocabulary extensible by a layer (5);
the privacy screen is shown, not acknowledged (15). Dropped: the five-phase `hz setup` journey, `--answers` files (flags
and `--json` cover agents), `hz setup --join`, membership by a pull request the member opens, org and
company as repositories of their own, core role templates.

## 4. Technical decisions (made here)

1. `hz join <repository>` is a top-level command (ADR 0018's second exception, with `hz setup`); `uvx heinzel join`
   works through a `heinzel` console script that is the same entry point as `hz`.
2. Identity: `gh api user` for the login (through the bound code host adapter until the vendor-CLI record is accepted),
   the office suite's account when bound; exact match on login or e-mail; one question on no or several matches, whose
   answer becomes a proposal pull request.
3. The manifest `hz.yaml` names the team plugin and its source; `join` installs it and its layers through the uv tool
   receipt (ADR 0006) after one yes; the layer declaration, the stack and playbooks are the other record's.
4. The `team` module provides `OrgContext` (checkout, manifest, roster, recommendations, the stack) and the commands
   `hz team new` (repository from the template, optionally a scaffolded team plugin), `hz team join`, `hz team add`,
   `hz team show` and `hz team adopt`; `hz join <url>` is the top-level front door and calls `team.join`. The `org`
   module has `hz org show` (the profile) and `hz org sync` (recommendations). `[team] repository` and
   `[team] checkout` replace `[workflow] team_repository`, which is deprecated; the checkout is fetched by the tick.
5. Data files people write are YAML (manifest, roster, people files), parsed with ADR 0021's strict loader;
   configuration stays TOML.
6. The first win is a contract run by hand (`hz workflow run`, ADR 0021 §12) from the stacked playbooks' template
   contracts, picked by role (the most specific layer that names one wins), copied into `me:`; the core ships none.
7. `join` is CLI and MCP only (it installs, clones and may open a pull request; it spends money for the win); `hz team
   show` and `hz org show` are in every head.
8. Tests run the journeys with `ScriptedPrompter`, fake vendor CLIs, the public reference company's plugins and a local
   bare Git repository; no test touches a code host or spends money.

## 5. How it maps onto the existing records

| Record | What onboarding uses | What changes |
| --- | --- | --- |
| ADR 0006 uv tool receipt | `uvx heinzel join` installs hz, then the team's stack, as uv tools | a second console script |
| ADR 0012 configuration | user file is the only place accepted recommendations are written | nothing |
| ADR 0014 skills | `hz-init` runs the invite line | the skill's script |
| ADR 0017, 0025 plugins | the team plugin is a plugin, `heinzel-acme-payments-checkout` | nothing |
| ADR 0018 namespace | `hz join` top level | amended (second exception) |
| ADR 0020 daemon | the team repository is the state repository; the Actions runner template ships with `team new` | nothing |
| ADR 0021 contracts | `me`, `team`, `schedule_team`, pinning, `run` by hand for the win | `team_repository` deprecated |
| ADR 0024 people | handles, roles, memberships, `people/<handle>.yaml`, minimisation | amended: roster drafted by hz and owned by the manager, matching by login, YAML data files, self-declaration as fallback |
| ADR 0028 bindings | written to the user file on acceptance; roster sources are capability reads | nothing here |
| ADR 0030 credentials | nothing in a repository is a secret; the CLIs own logins | nothing |
| ADR 0032 setup steps | `join` runs the steps the layers' adapters need | nothing |

## 6. Build plan

One slice per pull request, in order. Everything that asks a question builds on ADR 0032's `SetupLet`, `Prompter` and
`ConfigEditor`; the stack builds on the layer let and playbooks (in flight in the core); the roster sources on the
first-party adapters through their CLIs.

| Slice | Delivers | Depends on |
| --- | --- | --- |
| S1 `org` module | `[org]` settings, the profile (`hz org show`), doctor check (merged) | ADR 0032 (merged) |
| S2 `hz join` | identity discovery, roster match, the one question and its proposal, the one-yes install of the stack, `--json`, flags, `--non-interactive`; the `heinzel` console script | S1; the layer let; the code host adapter |
| S3 first win | the hook into playbooks' template contracts by role, the capped run, doctor and "what happens next" | S2; the playbook let; the reference company's playbook |
| S4 team repository | the `team` module: `hz team new`, `join`, `add`, `show`, `adopt`, `[team]` settings, `OrgContext`, the template (manifest, CODEOWNERS, roster, Actions runner), the roster drafted from a GitHub team (merged, #46); `recommend:` accepted with `hz org sync` (merged, #51); still to come: a scaffolded team plugin | S1 |
| S5 roster draft | redrafting from the tracker's team and the directory, as a diff pull request | S4; the tracker adapter through its CLI |
| S6 reference company | the journeys above run end to end against the public reference company's four plugins | S2–S5 |
| S7 agents and docs | `hz-init` runs the line; README onboarding section; e2e journeys on macOS and Linux | S2 |

## 7. Risks

- **A newcomer without a code host login** can't be matched or approve (ADR 0024 §4); they can still be in the roster
  and receive drafts.
- **A team without a tracker team or directory** types its roster once; hz still drafts newcomer entries from the no-match
  question.
- **Manifest and roster text are untrusted** like any repository content: parsed with the strict YAML loader of ADR 0021,
  shown as text, never executed or applied; the only thing `join` installs is the plugin the manifest names, after the yes.
- **The stack conflicts with what the person has** (another team's layers, a different site): the layer record's open
  precedence question; `join` shows the conflict and the person's file wins.
- **The win spends money**: asked once, cap shown, default contract small (one person's week).
- **`uvx heinzel`** depends on the console script shipping with the distribution; until then the line is
  `uvx --from heinzel hz join ...`, which the README says.

## Sources

Repository: `INTENT.md`, ADRs 0006, 0012, 0014, 0017, 0018, 0020, 0021, 0024, 0025, 0028, 0030 and 0032 and
`docs/research/people-and-org.md`, `daemon-head.md`; the maintainer's decisions of 2026-10-24.
