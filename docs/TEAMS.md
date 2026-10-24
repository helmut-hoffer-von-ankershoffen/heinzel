# Teams

A team keeps its shared state in one Git repository: its roster, each person's preferences, the team's workflow
contracts, inventory and policies. The repository's URL is the invite, and the code host's permissions decide who may
read it. Nothing in it configures anyone's machine.

## Join your team

Someone posts the team repository's URL in chat. You run:

```bash
hz team join https://github.com/acme-example/hz-team-payments.git
```

hz clones it with your own git access, reads who you are from the GitHub login `gh` is signed in as, and looks you up
in the roster. It then shows what is left: making it your team (`[team]` in your user configuration). Nothing waits
for you but that; add `--yes` to go ahead:

```bash
hz team join https://github.com/acme-example/hz-team-payments.git --yes
```

Not on the roster? You can still read the team's contracts. Ask to be added: `--propose` opens a pull request that adds
you, which the team's owners merge. If the roster lists you without a login, claim that entry with `--handle <entry>`.
`gh` not signed in? Say who you are with `--login <your GitHub login>`.

Your team's plugin, the one `hz.yaml` names, is your layer: `hz system layer show` shows it stacked on your
company's, org's and site's plugins ([Playbooks: layers](PLAYBOOKS.md#layers-company-org-site-team)). Install it with
`hz system plugin add <plugin>`; another layer is yours with `hz system config set team.layer <layer>`.

## Create a team

```bash
hz team new payments --github acme-example/hz-team-payments --roster github:acme-example/payments
```

This creates the repository from a template, makes you its first owner, and drafts the roster from a GitHub team you
can read; everyone drafted starts as `engineer`, and you fix roles in a commit. Without `--github` the repository
exists only on your machine until you push it.

The repository holds:

| Path | What | Who approves changes |
| --- | --- | --- |
| `hz.yaml` | the team's name, the roles that own the roster, its plugin if it has one | owners |
| `team/roster.yaml` | handle, GitHub login, roles and dates of each member; no names, titles or managers | owners |
| `people/<handle>.yaml` | each person's preferences | that person |
| `workflows/` | team contracts ([WORKFLOWS](WORKFLOWS.md)), checked by the repository's Actions workflow | owners and members |
| `inventory/`, `policies/` | declared artefacts and systems; owned policies | owners |

CODEOWNERS says who approves what. GitHub enforces it only when the default branch requires a pull request reviewed
by code owners; set that rule in the repository's settings.

## What your team recommends

A team's `hz.yaml` may recommend what its members use. It only recommends: nothing in it binds, installs, stores or
schedules anything on your machine.

```yaml
recommend:
  plugins: [heinzel-google]
  bindings: {documents: google, tracker: github}
  credentials: [google/oauth-client]   # names only, never values
  contracts: [weekly-status]           # team contracts in workflows/
```

`hz org sync` shows each recommendation beside what you have, and where it comes from. `--accept <ids>` (or `all`)
writes what you accept to your user file: a binding to `[capabilities]`, a contract to `[workflow] schedule_team`,
pinned to the content you read with `hz workflow show team:<name>`. `all` takes only what you have no choice for yet;
what you chose otherwise switches only when you name it. Plugins and credentials stay commands you run yourself, which
`sync` names. `hz system doctor` says when recommendations wait for you. An entry hz can't check is dropped, and
`hz team show` says which.

Your team's layer plugins recommend too: `sync` lists the capability bindings their playbooks recommend, the most
specific layer's for each capability, with the layer as the origin; the team repository's recommendation wins for the
same capability ([Playbooks: layers](PLAYBOOKS.md#layers-company-org-site-team)).

## Add someone, show, leave

- `hz team add theo --login theo-example --role engineer` adds someone to the roster, as an owner: a pull request, or a
  commit for a repository without a remote. `--by <your login>` says who you are when `gh` is signed in as someone else.
- `hz team show` lists the team, its checkout and its members, and what is wrong with them; `hz system doctor` checks
  the same.
- To leave: `hz system config unset team.checkout && hz system config unset team.repository`.

## Is the work flowing

Each run of a scheduled team contract is a promise: its artifact, by the next run of a contract that reads it (or else
its own next run). Your Heinzelmännchen report what they did, and `hz graph` says whether each promise is kept
([ADR 0054](adr/0054-observability-is-whether-each-team-promise-is-kept.md)):

```bash
hz system config set report.user costas                          # once: your roster handle
hz report drafted costas-tracker https://tracker.acme.example/boards/7#costas --contract team:tracker-update --part costas
hz report approved costas-tracker https://tracker.acme.example/boards/7#costas costas --contract team:tracker-update --part costas
hz report heartbeat costas-tracker                               # alive; silent for 2 days shows as offline
hz report sync                                                   # copy your logs into the checkout, then commit and push
hz graph                                                         # "Weekly report waiting on 2 updates", weeks, states
hz graph --heinzel costas-tracker --json                         # JSON Graph Format, docs/schemas/team-graph-1.json
```

A promise is `on_time`, `waiting_for_approval`, `late`, `fell_back` (kept on a stale input) or `offline`. The graph is the
team layer only: entries are metadata (who, when, which verb, on what), never content, descriptions reach no graph, and
nothing is counted per person. Your sessions, drafts and edits stay yours. Logs live in your state directory, one per
Heinzelmännchen, and in `telemetry/<handle>/<id>.jsonl` in the team repository, one writer each; `sync` refuses a copy
your log doesn't extend. Pushing is still yours; automatic sync is the next step.

The graph keeper watches it for you: `hz workflow new keeper --template graph:team-graph-keeper` writes a contract that
reads the graph and drafts a morning note for you alone; uncomment its schedule when its drafts read right.

## Your own state repository

hz notes, on your machine, the days you used it (one line a day: no time, no count, no command) and each version it
ran. Nothing of it leaves until you name a private repository of your own; then the daemon pushes what is new every
15 minutes to this machine's branch `hz/<handle>/<machine>`, fast-forward only, with your git credentials
([ADR 0062](adr/0062-state-repositories-one-writer-per-branch-the-daemon-pushes.md)):

```bash
hz system config set system.state.personal https://github.com/mira-example/heinzel-state-mira   # private, yours
hz system state push                                             # now, rather than within 15 minutes
hz system state status                                           # where it goes, what is pending, the last push
```

The machine id is random, never the host name. Each branch has one writer: a branch that moved to commits this
machine didn't make stops the push (`HZS001`) instead of being overwritten; delete the branch if they aren't yours,
then `hz system state reset`. Sharing with your team comes next, only after you say yes, and aggregate unless you say
yes again to being named.

## What waits for your yes

`new`, `join` and `add` show every step, with the command it runs, before it happens. Reading (a clone into hz's data
directory) runs at once. Steps that change something outside your machine (creating a repository, pushing, opening a
pull request) and making a team yours wait for `--yes`. The commands are CLI and MCP only.

An existing checkout from the deprecated `[workflow] team_repository` becomes your team with `hz team adopt <path>`.
