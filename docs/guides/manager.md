# Bootstrap your team

For the manager who sets Heinzelmännchen up for a team: install `hz`, sign in to the tools you already use, create
the team repository, get the first draft out every Friday, then invite the team with one line. Nothing is published,
sent or scheduled unless you say so.

Examples use a made-up company, ACME (GitHub organisation `acme-example`) and its Checkout team.

## The 10-minute path

You need [uv](https://docs.astral.sh/uv/) (`brew install uv`), the [GitHub CLI](https://cli.github.com)
(`brew install gh`), the right to create a private repository in your GitHub organisation, and, for drafting, the
[Claude Code](https://docs.claude.com/en/docs/claude-code) CLI installed and signed in (the default agent runner).

Start the workspace with:

```sh
uvx heinzel@latest
```

Leave it running and use another terminal:

```bash
gh auth login                                            # once; hz stores no token
hz setup                                                 # who you are: found from gh, the rest asked once
hz system plugin add github --confirm                    # pull requests and issues through gh
hz system config set capabilities.code_host github
hz system config set capabilities.tracker github
hz team new checkout --github acme-example/hz-team-checkout --roster github:acme-example/checkout --yes
hz heinzel new weekly-report --team                      # the team's first contract, a commented starter
```

`--yes` creates the private repository and pushes it; drop it to see the plan first (step 4). Open
`<checkout>/workflows/weekly-report.prose.md` (`hz team show` names the checkout), uncomment `requires` and the
`code-host` and `tracker` inputs, set your repository and project, and write the Goal. Then:

```bash
hz heinzel check                                        # every problem by file and line
hz heinzel wake team:weekly-report --dry-run            # what the helper would get; nothing is spent
hz heinzel wake team:weekly-report                      # the first draft, a local file
hz system doctor
```

Commit and push the contract (step 7), then post one line in the team's channel (step 8).

## 1. Install hz

`uvx heinzel@latest` installs the standard app and its default plugins on first run and opens the workspace.
`hz --version` shows the version; `hz system update` keeps it current ([installation guide](install.md)).

## 2. Sign in to the tools you already use

hz reads and writes as you, through each vendor's own CLI, as an ordinary employee. hz never sees or stores a token, and no administrator has to register an app. Sign in once:

| Tool | For | Sign in |
| --- | --- | --- |
| [GitHub CLI](https://cli.github.com) `gh` | pull requests, issues, the team repository, the roster draft | `gh auth login` (or `gh auth login --hostname ghe.example.com`) |
| [Atlassian CLI](https://developer.atlassian.com/cloud/acli/) `acli` | Jira issues, Confluence pages | `acli jira auth login --web` and `acli confluence auth login --web` |
| [Google Workspace CLI](https://github.com/googleworkspace/cli) `gws` | Docs, Sheets, Drive, Calendar, Gmail | `npm install -g @googleworkspace/cli`, then `hz setup google` signs it in with read-only scopes |

`hz system doctor` reports each CLI found and its stored sign-in, without contacting the vendor. Nothing is sent home.

## 3. `hz setup`: tell hz who you are

```bash
hz setup
```

`hz setup` is one resumable journey. It looks up your `gh` and `gws` logins and this machine's clock, and asks only
for the rest: your handle, your role (`engineering-manager` for you) and your working hours. Each installed plugin's
step follows and can be skipped; a later run continues where the last stopped.

`hz org show` shows the profile. Without a person at the terminal, use `hz setup --answers answers.toml`
(`--print-answers` writes that file from a finished setup).

## 4. Choose plugins and create the team

The standard app installs Clock, Resource, Tailscale and the theme. Add the integrations you use:

```bash
hz system plugin search                      # first-party plugins first, then community ones from GitHub
hz system plugin add github --confirm        # pull requests and issues: code-host and tracker
hz system plugin add atlassian --confirm     # Jira and Confluence: tracker and documents
hz system plugin add google --confirm        # Docs, Sheets, Drive, Calendar, Gmail
hz system plugin add zoom --confirm          # meetings, from Zoom's summary e-mails
hz setup google                              # a plugin's own step; `hz setup` runs the ones still missing
```

Bind each capability to the adapter that serves it, in your user configuration (a project `hz.toml` can't bind). The first two are in the 10-minute path:

```bash
hz system config set atlassian.site_url https://acme-example.atlassian.net   # the site acli is signed in to
hz system config set capabilities.documents atlassian     # or google
hz system config set capabilities.meetings zoom           # needs capabilities.mail, which `hz setup google` offers
hz system capability list                                 # every capability, its adapter and who reads what
```

Each plugin's README is in [heinzel-plugins](https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/tree/main/plugins).

Now create the team repository. Its URL is the invite; the code host's permissions decide who may read it.

```bash
hz team new checkout --github acme-example/hz-team-checkout --roster github:acme-example/checkout
```

Without `--yes`, nothing runs. You see the plan, each step with the command it runs:

```text
hz team new: checkout
  to do  create a Git repository
  to do  write the template: CODEOWNERS, README.md, hz.yaml, team/roster.yaml, ...
  to do  create the private repository on GitHub and push main  (remote)  (waits for your yes)
```

With `--yes`, every step shows `done`, then the drafted roster and the invite line.

- **You own the roster.** `--roster github:<org>/<team-slug>` drafts it from a GitHub team you can read. Everyone
  besides you starts as `engineer`; fix roles in a commit, and add later joiners with `hz team add`.
- **You are the first owner.** `hz team add <handle> --login <login> --owner` adds another.
- **Set the code-owner rule.** `CODEOWNERS` is documentation until `main` requires a pull request reviewed by code
  owners. Add that rule in the repository's settings.
- Without `--github`, the repository stays on your machine until you push it.

`hz team show` lists the team and what is wrong with it. The repository's layout and roster format are in
[TEAMS](../TEAMS.md).

## 5. Recommend, don't configure: `hz.yaml`

A team's `hz.yaml` may recommend what its members use. It only recommends: nothing in a repository binds, installs,
stores or schedules anything on anyone's machine. Edit it in the checkout and commit:

```yaml
plugin: {name: heinzel-acme, source: git+https://github.com/acme-example/heinzel-acme}
recommend:
  plugins: [heinzel-github, heinzel-atlassian]
  bindings: {code-host: github, tracker: github, documents: atlassian}
  contracts: [weekly-report]           # team contracts in workflows/ (step 7)
```

Each teammate accepts with `hz org sync`. What it shows and writes is in
[TEAMS: what your team recommends](../TEAMS.md#what-your-team-recommends).

## 6. The first contract: this week's report

A **contract** keeps one asset true, in a `<name>.prose.md` file: frontmatter says what it reads and when, the body
what the draft must be ([Heinzelmännchen](../HEINZELMAENNCHEN.md)). A **playbook** is what a plugin brings so you
don't start blank: asset types, template contracts and recommended settings ([Playbooks](../PLAYBOOKS.md)).

```bash
hz heinzel playbooks                        # what the installed plugins offer
hz heinzel discover                         # candidates for each asset type, through what you have bound
hz heinzel new weekly-report --template <module>:weekly-report --asset weekly-report=<document-ref>
```

`playbooks` prints a table per playbook: asset types, templates and recommended settings. The reference company ships a
weekly-report playbook in its checkout plugin (step 9), so `playbooks` lists it once you install that plugin. Without
it, `playbooks` says "No playbooks yet." and you start from the starter contract, valid as written.

If you followed the 10-minute path, the team's `weekly-report` exists already; use `team:weekly-report` below. A
personal contract (`me:`) starts with `hz heinzel new weekly-report`. A bare name in both namespaces is an error that
asks for `me:` or `team:`.

```bash
hz heinzel check                            # every problem by file and line; empty when all is well
hz heinzel show team:weekly-report          # what it requires, what is bound, whether it is ready
hz heinzel wake team:weekly-report --dry-run # fetches the inputs and prints the packet; nothing is spent
hz heinzel wake team:weekly-report          # drafts it now, within `[heinzel] max_usd` (1 USD by default)
hz heinzel drafts --name weekly-report --latest
```

Uncomment the inputs you want (merged pull requests through `code-host`, changed items through `tracker`, last
week's report through `documents`) and write the Goal. The run reports drafting time, cost and the draft's path.
`hz system doctor` tells you when the agent runner (Claude Code by default) isn't found.

**Schedule it.** In a personal contract (`me:`), set `schedule: "0 17 * * FRI"` in the frontmatter: five cron fields,
local time. The daemon drafts at its first tick after that time, so schedule a little earlier. Members schedule a team
contract through `hz org sync`.

```bash
hz heinzel show me:weekly-report            # now names the daemon job, workflow:me-weekly-report
hz system daemon status                      # installed? If not:
hz system daemon install                     # launchd or systemd runs `hz system daemon tick` every few minutes
hz system daemon tick --job workflow:me-weekly-report   # run it once as the daemon would, with a receipt
```

`hz system daemon jobs` lists the job with its next run. The daemon runs your jobs on a schedule with your rights
([SECURITY](../../SECURITY.md)); `hz system daemon pause` stops every job.

## 7. Send it, measure it, make it the team's

A draft is a local file: edit it and send it the way you always do. Then tell hz what you sent, so the thin slice can
prove itself after three Fridays ([INTENT](../../INTENT.md#where-we-are)):

```bash
hz heinzel sent weekly-report --file ~/reports/week-44.md --edit-minutes 10
hz heinzel sent weekly-report --ref <document-ref>       # or the approved version where it lives, through documents
hz heinzel show weekly-report                            # the Success test table
```

`sent` reports the share of words changed. `show` gains a **Success test** table with each draft's drafting time, cost,
share changed and your editing estimate. It stays on your machine and is about the drafts, never about people.

A contract in the team's `workflows/` is everyone's to run by hand and each person's to schedule. The 10-minute path
wrote it there already; from a personal contract, run `hz heinzel new weekly-report --team`. Then:

```bash
hz heinzel show team:weekly-report          # prints its SHA-256; members pin it with `hz org sync --accept contract:weekly-report`
```

Commit and push the contract, or open a pull request when `main` is protected; the repository's Actions workflow runs
`hz heinzel check`. Add it to `recommend.contracts` (step 5) and `hz org sync` offers it to every member.

## 8. Invite the team: one line in chat

Post one line in the team's channel, the same for everyone. It runs hz without installing it, finds out who the person is, joins the team and sets up what is missing:

```bash
uvx heinzel@latest join https://github.com/acme-example/hz-team-checkout.git
```

Whoever can read the repository can join. There are no invite tokens and no server. A teammate with `hz` installed runs
`hz join <url>` instead. Everything they need is in [Join your team](joining.md).

Joiners appear in `hz team show`. A newcomer the roster lacks asks to be added with a pull request you merge.

## 9. A second team: org and company layers

Anything specific to a company, org, site or team is a plugin, never part of the core. A company keeps its plugins in
one private repository, laid out like
[heinzel-plugins](https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/tree/main/plugins):

```bash
hz system plugin new ../heinzel-acme               # scaffold one
hz system plugin add git+https://github.com/acme-example/heinzel-acme@v1.0.0 --confirm   # install a released one
```

Plugins are **layers**: one per company, org, site and team, each declaring where it sits
(`module.layer(parent="acme-payments", site="acme-lisbon")`) and depending on its parent and site. What teams share
moves up a layer: the company plugin binds shared adapters, the org plugin brings the shared playbook, a site plugin
sets time zone and working hours, a team plugin only its own contract and roles. Nothing a layer brings is applied
without the person's yes ([Playbooks: layers](../PLAYBOOKS.md#layers-company-org-site-team)).

The reference company, [heinzel-acme](https://github.com/helmut-hoffer-von-ankershoffen/heinzel-acme), is the worked
example. It ships the company plugin, `heinzel-acme-payments`, `heinzel-acme-lisbon` and
`heinzel-acme-payments-checkout`, with the weekly-report playbook and a runbook,
[`docs/PORT_TO_YOUR_COMPANY.md`](https://github.com/helmut-hoffer-von-ankershoffen/heinzel-acme/blob/main/docs/PORT_TO_YOUR_COMPANY.md).

Name the team's plugin in each team repository's `hz.yaml` (step 5), so joiners are offered it. Start the second team like the first:

```bash
hz team new billing --github acme-example/hz-team-billing --roster github:acme-example/billing --yes
hz system layer list                         # every installed layer, where it sits, what it brings, what is wrong
hz system layer show                         # your stack, general to specific, with the settings each layer recommends
```

`show` prints the stack, `acme → acme-payments + acme-lisbon → acme-payments-checkout`. `hz system doctor` names a
missing parent or site or a cycle, each with its fix; `hz system config set team.layer <layer>` picks another layer.

## What's next

- Rather click? `hz ui` opens a web app on localhost, for this session only.
- Every command takes `--json`, and `hz system serve mcp` gives coding agents the same operations as MCP tools.
- By default `hz` lists only the everyday commands. `hz system expert on` lists the rest, such as `hz heinzel
  playbooks`, `discover` and `sent`, and `hz system plugin`, `config`, `daemon` and `layer`. They run either way.
- All commands: [CLI_REFERENCE](../CLI_REFERENCE.md). Every setting: [CONFIGURATION](../CONFIGURATION.md). Decisions:
  [ADRs](../adr/README.md).

## Not built yet

- Drafting the roster from the recurring 1:1s in your calendar. Today you fix roles by hand in a commit.
- Approving a draft in place, where hz writes it into this week's section of the rolling document with a draft tag.
  Today a draft is a local file.
- The first draft for a joiner at the end of `join`.
