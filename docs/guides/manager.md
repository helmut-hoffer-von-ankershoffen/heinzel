# Bootstrap your team

For the manager who sets Heinzelmännchen up for a team: install `hz`, sign in to the tools you already use, create
the team repository, get the first draft out every Friday, then invite the team with one line. Every step says what
you should see. Nothing is published, sent or scheduled unless you say so.

Examples use a made-up company, Acme (GitHub organisation `acme-example`) and its Checkout team. What is not in this
release is marked **coming in the next release**, with what to do meanwhile.

## The 10-minute path

You need [uv](https://docs.astral.sh/uv/) (`brew install uv`), the [GitHub CLI](https://cli.github.com)
(`brew install gh`), the right to create a private repository in your GitHub organisation, and, for drafting, the
[Claude Code](https://docs.claude.com/en/docs/claude-code) CLI installed and signed in (the default agent runner).

```bash
uv tool install heinzel
gh auth login                                            # once; hz stores no token
hz setup                                                 # who you are: found from gh, the rest asked once
hz system plugin add github --confirm                              # pull requests and issues through gh
hz system config set capabilities.code_host github
hz system config set capabilities.tracker github
hz team new checkout --github acme-example/hz-team-checkout --roster github:acme-example/checkout --yes
hz heinzel new weekly-report --team                     # the team's first contract, a commented starter
```

`--yes` creates the private repository and pushes it; drop it to see the plan first (step 5). Now open
`<checkout>/workflows/weekly-report.prose.md` (`hz team show` names the checkout): uncomment `requires` and the
`code-host` and `tracker` inputs, put your repository and project in their arguments, write the Goal; the starter
explains each key. Then:

```bash
hz heinzel check                                        # every problem by file and line
hz heinzel wake team:weekly-report --dry-run             # what the agent would get; nothing is spent
hz heinzel wake team:weekly-report                       # the first draft, a local file
hz system doctor
```

Commit and push the contract (step 8), then post one line in the team's channel (step 9). The rest of this page is
the same path with what you should see, and the choices along the way.

## 1. Install hz

You need [uv](https://docs.astral.sh/uv/) (`brew install uv`).

```bash
uv tool install heinzel
hz --version
```

What you should see: the version on the first line (hz 0.x.y), then the repository, Python, platform and installation, and one line
per installed plugin (none on a fresh install). Pin a release with `@vX.Y.Z`; `hz system update` then moves to the latest release and keeps your plugins. From
Claude Code, `/hz-init` does the same ([README](../../README.md#install)).

## 2. Sign in to the tools you already use

hz reads and writes as you, through each vendor's own CLI, as an ordinary employee
([ADR 0035](../adr/0035-integrations-go-through-vendor-clis-as-an-ordinary-employee.md)). The CLI owns the login; hz
never sees, stores or asks for a token, and no administrator has to register an app. Install and sign in once:

| Tool | For | Sign in |
| --- | --- | --- |
| [GitHub CLI](https://cli.github.com) `gh` | pull requests, issues, the team repository, the roster draft | `gh auth login` (or `gh auth login --hostname ghe.example.com`) |
| [Atlassian CLI](https://developer.atlassian.com/cloud/acli/) `acli` | Jira issues, Confluence pages | `acli jira auth login --web` and `acli confluence auth login --web` |
| [Google Workspace CLI](https://github.com/googleworkspace/cli) `gws` | Docs, Sheets, Drive, Calendar, Gmail | `npm install -g @googleworkspace/cli`, then `hz setup google` signs it in with read-only scopes ([README](../../plugins/heinzel-google/README.md)) |

What you should see: `gh auth status` names your login; `hz system doctor` reports each CLI found, its version and
its stored sign-in, without contacting the vendor.

What hz keeps about you is your profile, in your user configuration on this machine; logins stay in `gh`, `acli`
and `gws`; nothing is sent home ([ADR 0027](../adr/0027-nothing-is-sent-home.md)).

## 3. `hz setup`: tell hz who you are

```bash
hz setup
```

`hz setup` is one resumable journey ([ADR 0032](../adr/0032-modules-declare-interactive-setup-steps.md)): it runs
every step not finished yet, who-you-are first, and a later run continues where the last one stopped. The profile step
looks up your `gh` and `gws` logins and this machine's clock, shows what it found (GitHub login, name, e-mail, time
zone) and asks only for the rest: your handle (your GitHub login is a good one), your role (`engineering-manager`
for you) and your working hours. Each installed plugin's step follows and can be skipped.

What you should see: first the privacy line ("hz keeps your profile and answers on this machine only…"), then
"Found about you:", the questions, "hz keeps this about you, on this machine only" with each value, and
`hz system doctor` at the end. `hz org show` shows the profile later; `hz system config set org.role
engineering-manager` corrects one part. For a terminal without a person (a coding agent, a team's preset answers):
`hz setup --answers answers.toml`; `hz setup --print-answers > answers.toml` writes that file from a finished setup;
`hz setup --restart` goes through every step again.

## 4. Choose plugins

None is installed by default, so you get only the integrations you use:

```bash
hz system plugin search                      # first-party plugins first, then community ones from GitHub
hz system plugin add github --confirm                  # pull requests and issues: code-host and tracker
hz system plugin add atlassian --confirm               # Jira and Confluence: tracker and documents
hz system plugin add google --confirm                  # Docs, Sheets, Drive, Calendar, Gmail: documents, calendar and mail
hz system plugin add zoom --confirm                    # meetings, from Zoom's summary e-mails through the bound mail adapter
hz system plugin add clock --confirm                   # the time for everyone on the team
hz setup google                              # a plugin's own step; `hz setup` runs the ones still missing
```

Bind each capability to the adapter that serves it, in your user configuration (a project `hz.toml` can't bind);
`hz setup google` binds its own on your yes, the others are one line each:

```bash
hz system config set capabilities.code_host github
hz system config set capabilities.tracker github          # or atlassian
hz system config set atlassian.site_url https://acme-example.atlassian.net   # the site acli is signed in to
hz system config set capabilities.documents atlassian     # or google
hz system config set capabilities.meetings zoom           # needs capabilities.mail, which `hz setup google` offers
hz system capability list                                 # every capability, its adapter and who reads what
```

What you should see: `hz system plugin list` names the plugins and the modules they bring; `hz system doctor` has a
line per plugin (`gh` found, its version and login; bindings in place). Each plugin's README has the details:
[heinzel-github](../../plugins/heinzel-github/README.md), [heinzel-atlassian](../../plugins/heinzel-atlassian/README.md),
[heinzel-google](../../plugins/heinzel-google/README.md), [heinzel-zoom](../../plugins/heinzel-zoom/README.md).

**Your company's own plugins.** Anything specific to a company, org, site or team is a plugin, never part of the
core ([INTENT: tenets](../../INTENT.md#tenets)). A company keeps its plugins in one private repository, laid out like
this repository's [plugins/](../../plugins), and everyone installs from it:

```bash
hz system plugin new ../heinzel-acme               # scaffold one: tests, CI, release automation, agent setup
hz system plugin add git+https://github.com/acme-example/heinzel-acme@v1.0.0 --confirm   # install a released one
```

A company's plugins are **layers** ([ADR 0040](../adr/0040-layers-and-playbooks-are-plugins.md),
[PLAYBOOKS: layers](../PLAYBOOKS.md#layers-company-org-site-team)): one plugin per company, org, site and team, each
declaring where it sits (`module.layer(parent="acme-payments", site="acme-lisbon")`) and listing its parent and site
as dependencies, so installing the team's plugin installs its stack. For one team, one plugin is enough; step 10 adds
the layers when the second team appears. The public reference company,
[heinzel-acme](https://github.com/helmut-hoffer-von-ankershoffen/heinzel-acme), is the worked example: a company
plugin `acme`, an org `acme-payments`, a team `acme-payments-checkout` and a site `acme-lisbon`, with adapters bound
by default, a weekly-report playbook and a runbook for porting it to your own company. Today it is the scaffold with
the company plugin; its org, team and site layers and the runbook are **coming in the next release**.

## 5. `hz team new`: the team repository

A team keeps its shared state in one Git repository: roster, each person's preferences, team contracts, policies
([TEAMS](../TEAMS.md), [ADR 0024](../adr/0024-people-and-org.md)). The repository's URL is the invite; the code host's
permissions decide who may read it.

```bash
hz team new checkout --github acme-example/hz-team-checkout --roster github:acme-example/checkout
```

Without `--yes` nothing runs: you see the plan, each step with the command it runs and whether it leaves your
machine:

```text
hz team new: checkout
Checkout: <data dir>/team/checkout
  to do  create a Git repository
         $ git init --quiet --initial-branch=main <data dir>/team/checkout
  to do  write the template: CODEOWNERS, README.md, hz.yaml, people/mira-example.yaml, team/roster.yaml, ...
  to do  commit them
  to do  create the private repository acme-example/hz-team-checkout on GitHub  (remote)  (waits for your yes)
  to do  push main to it  (remote)  (waits for your yes)
  to do  make it your team in your user configuration ([team])  (waits for your yes)
The rest waits for your yes; to go ahead: hz team new checkout --github acme-example/hz-team-checkout --roster github:acme-example/checkout --yes
```

With `--yes` (as in the 10-minute path) every step shows `done`, then the drafted roster for you to review, "Invite
your team with one line in chat: hz team join …" and the reminder about the code-owner branch rule (below).

- **The roster is drafted, you own it.** `--roster github:<org>/<team-slug>` reads the GitHub team you can read
  (`github:<org>` an organisation); everyone besides you starts as `engineer`, you fix roles in a commit
  (`hz team add` for later joiners; the roles: `engineer`, `principal`, `engineering-manager`,
  `engineering-leadership`, `product-manager`, `product-leadership`, `program-manager`). Guesses from the recurring
  1:1s in your calendar, for you to confirm, are **coming in the next release**: the `directory` capability's
  `one-on-ones` adapter exists, no command drafts a roster from it yet. The roster holds handle, GitHub login, an
  optional e-mail, roles and dates: never names, titles or managers.
- **You are the first owner.** Owners approve roster changes; `hz team add <handle> --login <login> --owner` adds
  another.
- **CODEOWNERS caveat.** The repository's `CODEOWNERS` says the owners approve `hz.yaml`, `team/` and `policies/` and
  each person their own `people/<handle>.yaml`. GitHub enforces it only when `main` requires a pull request reviewed
  by code owners: add that rule in the repository's settings (hz prints this reminder after `new`), or CODEOWNERS is
  documentation.
- Without `--github` the repository exists only on your machine until you push it; `new` prints the `gh repo create`
  and `hz team adopt` commands for later.

`hz team show` lists the team, its checkout and members, and what is wrong with them; `hz system doctor` checks the
same.

## 6. Recommend, don't configure: `hz.yaml`

A team's `hz.yaml` may recommend what its members use. It only recommends: nothing in a repository binds, installs,
stores or schedules anything on anyone's machine ([ADR 0012](../adr/0012-layered-configuration.md)). Edit it in the
checkout and commit:

```yaml
name: checkout
owners: [engineering-manager]
plugin: {name: heinzel-acme, source: git+https://github.com/acme-example/heinzel-acme}
recommend:
  plugins: [heinzel-github, heinzel-atlassian]
  bindings: {code-host: github, tracker: github, documents: atlassian}
  credentials: []                      # names only, never values
  contracts: [weekly-report]           # team contracts in workflows/, once you have one (step 8)
```

Each teammate accepts with `hz org sync`, which shows every recommendation beside what they have and writes only what
they accept (`--accept all`, or one by id such as `--accept binding:documents`) to their user file; plugins and
credentials stay commands they run themselves, which `sync` names. `hz system doctor` says when recommendations wait
for someone. Details: [TEAMS: what your team recommends](../TEAMS.md#what-your-team-recommends).

## 7. The first contract: this week's report

A **contract** keeps one asset true, in a `<name>.prose.md` file: frontmatter says what it reads and when, the body
what the draft must be ([WORKFLOWS](../HEINZELMAENNCHEN.md)). A **playbook** is what a plugin brings so you don't start
blank: asset types, hints to find yours, template contracts, recommended settings
([PLAYBOOKS](../PLAYBOOKS.md), [ADR 0037](../adr/0037-plugins-bring-playbooks.md)).

```bash
hz heinzel playbooks                        # what the installed plugins offer
hz heinzel discover                         # candidates for each asset type, through what you have bound
hz heinzel new weekly-report --template <module>:weekly-report --asset weekly-report=<document-ref>
```

What you should see: a table per playbook (asset types, templates with the `hz heinzel new <name> --template <module>:<template>` command
that uses them, recommended settings "only if you agree"), then the path of the new contract and what to run next.
No first-party plugin brings a playbook yet, so today `playbooks` says "No playbooks yet."; the reference company's
weekly report is the first and is **coming in the next release**. Meanwhile start from the starter contract, which is
valid as written and commented. The commands below write and run a personal contract (`me:`); if you followed the
10-minute path, the team's `weekly-report` exists already: skip `new` and say `team:weekly-report` where a bare name
appears below, since a bare name that exists in both namespaces is an error that asks for `me:` or `team:`.

```bash
hz heinzel new weekly-report                # writes weekly-report.prose.md into your workflows directory
hz heinzel check                            # every problem by file and line; empty when all is well
hz heinzel show weekly-report               # what it requires, what is bound, whether it is ready, what to run next
```

Uncomment the inputs you want (merged pull requests through `code-host`, changed items through `tracker`, last
week's report through `documents`), write the Goal and Done-when sections, then:

```bash
hz heinzel wake weekly-report --dry-run      # fetches the inputs and prints the packet; no agent runs, nothing spent
hz heinzel wake weekly-report                # drafts it now, within `[heinzel] max_usd` (1 USD by default)
hz heinzel drafts --name weekly-report --latest
```

What you should see: the dry run prints the instructions and the inputs as the agent would get them; the run reports
drafting time and cost, and the draft's path under `<data dir>/workflow/drafts/me-weekly-report/` with a
`.record.json` beside it (inputs, their hashes, the agent's report). `hz system doctor` tells you when the agent
runner (Claude Code by default) isn't found.

**Schedule it.** Set `schedule: "0 17 * * FRI"` in the frontmatter (five cron fields, local time; the daemon drafts
at its first tick after, so schedule a little earlier to have it waiting at 17:00), then:

```bash
hz heinzel show weekly-report               # now names the daemon job, workflow:me-weekly-report
hz system daemon status                      # installed? If not:
hz system daemon install                     # launchd or systemd runs `hz system daemon tick` every few minutes
hz system daemon tick --job workflow:me-weekly-report   # run it once as the daemon would, with a receipt
```

What you should see: `status` says whether the daemon is installed and loaded and when it last ticked;
`hz system daemon jobs` lists the job with its next run. The daemon runs unattended with your rights
([SECURITY](../../SECURITY.md)); `hz system daemon pause` stops every job until you resume.

## 8. Drafts, approval and the success measure

Today a draft is a local file: edit it and send it the way you always do. Approval in place, where hz writes the
draft into this week's section of the rolling document with a draft tag and your edit removes the tag, is
**coming in the next release** ([ADR 0038](../adr/0038-drafts-are-approved-in-place.md)); the contract stays the
same, only where the draft lands changes.

Then tell hz what you sent, so the slice can prove itself after three Fridays
([INTENT: thin slice first](../../INTENT.md#where-we-are)):

```bash
hz heinzel sent weekly-report --file ~/reports/week-44.md --edit-minutes 10
hz heinzel sent weekly-report --ref <document-ref>       # or the approved version where it lives, through documents
hz heinzel show weekly-report                            # the Success test table
```

What you should see: `sent` reports the share of words changed and both word counts; `show` gains a **Success test**
table with each draft's drafting time, cost, share changed and your editing estimate, with the median and mean
below. It stays on your machine and is about the drafts, never about people.

**Make it the team's, before you invite.** A new team repository has no contracts; a contract in its `workflows/` is
everyone's to run by hand and each person's to schedule, and it is what a joiner's first win reads. The 10-minute
path wrote it there already; from a personal contract, do it now:

```bash
hz heinzel new weekly-report --team         # writes <checkout>/workflows/weekly-report.prose.md
hz heinzel show team:weekly-report          # prints its SHA-256; members pin it with `hz org sync --accept contract:weekly-report`
```

Commit and push it (or open a pull request, when `main` is protected); the repository's Actions workflow runs
`hz heinzel check`. Add it to `recommend.contracts` (step 6) and `hz org sync` offers it to every member, pinned to
the content they read.

## 9. Invite the team: one line in chat

Post one line in the team's channel, the same for everyone; `hz join` prints it for the next person. It runs this
release of hz without installing it, says what hz keeps about the person, finds out who they are from their `gh` and
`gws` logins, joins the team and sets up what is still missing:

```bash
uvx --from heinzel heinzel join https://github.com/acme-example/hz-team-checkout.git
```

The line `hz join` prints is pinned to your release (`heinzel@vX.Y.Z`); this is its shape. Whoever can read the
repository can join; whoever can't gets the code host's answer. There are no invite tokens and no server. Everything
they need is in [Join your team](joining.md). A teammate with `hz` installed runs `hz join <url>` instead;
`hz team join <url>`, the line `hz team new` printed, is the lower-level step that only clones and matches the roster.

What you should see: each joiner appears in `hz team show` once they are on the roster; a newcomer the roster lacks
asks to be added, which opens a pull request you merge. The first win at the end of `join`, a draft of the joiner's
own part of the week from the team's playbook, is **coming in the next release**.

## 10. A second team: org and company layers

When a second team appears, what the teams share moves one layer up: the company plugin binds the adapters everyone
uses (code host, tracker, documents, calendar), the org plugin brings the playbook with the weekly report and board
hygiene the org's teams share, a site plugin the time zone and working hours, and each team plugin only its own
template contract and roles. Names follow the path (`acme`, `acme-payments`, `acme-payments-checkout`, `acme-lisbon`),
the stack is combined general to specific, and nothing a layer brings is applied without the person's yes
([ADR 0040](../adr/0040-layers-and-playbooks-are-plugins.md), [PLAYBOOKS: layers](../PLAYBOOKS.md#layers-company-org-site-team)).

In the company's plugin repository (step 4), give each layer its own plugin that declares where it sits and depends
on its parent and site; then name the team's plugin in each team repository's `hz.yaml` (`plugin:`, step 6), so
joiners are offered it. Start the second team the same way as the first:

```bash
hz team new billing --github acme-example/hz-team-billing --roster github:acme-example/billing --yes
hz system layer list                         # every installed layer, where it sits, what it brings, what is wrong
hz system layer show                         # your stack, general to specific, with the settings each layer recommends
```

What you should see: `show` prints the stack, `acme → acme-payments + acme-lisbon → acme-payments-checkout`, the
roles it adds and the settings its playbooks recommend, most specific first. `hz org sync` lists the bindings among
them beside what each person has, and `hz system doctor` names a missing parent or site, a cycle, or an org and a
site that disagree while the team is silent, each with its fix. Your layer is the plugin your team's `hz.yaml` names;
`hz system config set team.layer <layer>` picks another. Template contracts the layers' playbooks bring are offered by
`hz setup`; the reference company's are **coming in the next release**.

## What's next

- Rather click? `hz ui` opens a web app on localhost, for this session only: settings, the contract graph, your
  team, the doctor's fixes as commands to copy, and a chat with your agent runner that says what it may cost first.
- Every command takes `--json`, and `hz system serve mcp` gives coding agents the same operations as MCP tools.
- Expert mode: `hz expert on` is **coming in the next release**. By default `hz` will then show only the everyday
  commands (join, setup, ui, team show, org, workflow list/show/drafts/run, doctor, update), and expert mode the
  rest. The manager's commands on this page beyond those (`hz team new` and `add`, `hz heinzel new`, `playbooks`
  and `discover`, `hz system plugin`, `config` and `daemon`) will need it once; today every command shows.
- All commands: [CLI_REFERENCE](../CLI_REFERENCE.md); every setting: [CONFIGURATION](../CONFIGURATION.md); the
  decisions: [ADRs](../adr/README.md).
