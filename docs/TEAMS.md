# Teams

A team keeps its shared state in one Git repository. The repository's URL is the invite, and the code host's
permissions decide who may read it. Nothing in it configures anyone's machine.

To create a team, see [Bootstrap your team](guides/manager.md); to join one, [Join your team](guides/joining.md).

## The repository

| Path | What | Who approves changes |
| --- | --- | --- |
| `hz.yaml` | the team's name, the roles that own the roster, its plugin if it has one, recommendations | owners |
| `team/roster.yaml` | one entry per member | owners |
| `people/<handle>.yaml` | that person's preferences | that person |
| `workflows/` | team contracts ([WORKFLOWS](HEINZELMAENNCHEN.md)), checked by the repository's Actions workflow | owners and members |
| `inventory/`, `policies/` | declared artefacts and systems; owned policies | owners |

`CODEOWNERS` says who approves what. GitHub enforces it only when the default branch requires review by code owners.

## The roster

Each entry holds a handle, a GitHub login, an optional e-mail, roles and `since`/`until` dates. It never holds names,
titles or managers. The roles are `engineer`, `principal`, `engineering-manager`, `engineering-leadership`,
`product-manager`, `product-leadership` and `program-manager`. Leaving sets `until`; handles are never reused.

- `hz team add theo --login theo-example --role engineer` adds someone, as a pull request or, without a remote, a
  commit.
- `hz team show` lists the team, its checkout and its members, and what is wrong with them.
- To leave: `hz system config unset team.checkout && hz system config unset team.repository`.

## What your team recommends

A team's `hz.yaml` may recommend what its members use. Nothing in it binds, installs or schedules anything on your machine.

```yaml
recommend:
  plugins: [heinzel-google]
  bindings: {documents: google, tracker: github}
  credentials: [google/oauth-client]   # names only, never values
  contracts: [weekly-status]           # team contracts in workflows/
```

`hz org sync` shows each recommendation beside what you have. `--accept <ids>` (or `all`) writes what you accept to your
user file: a binding to `[capabilities]`, a contract to `[heinzel] schedule_team`, pinned to the content you read with
`hz heinzel show team:<name>`. `all` takes only what you have no choice for yet. Plugins and credentials stay commands
you run yourself, which `sync` names. `hz system doctor` says when recommendations wait for you.

Your team's plugin, the one `hz.yaml` names, is your layer. `sync` also lists the bindings your layers' playbooks
recommend, most specific first; the team repository wins for the same capability
([Playbooks: layers](PLAYBOOKS.md#layers-company-org-site-team)).

## Is the work flowing

Each run of a scheduled team contract is a promise: its artifact, by the next run of a contract that reads it. Your
Heinzelmännchen report what they did, and `hz graph` says whether each promise is kept
([why](adr/0054-observability-is-whether-each-team-promise-is-kept.md)):

```bash
hz system config set report.user costas                          # once: your roster handle
hz report drafted costas-tracker https://tracker.acme.example/boards/7#costas --contract team:tracker-update --part costas
hz report approved costas-tracker https://tracker.acme.example/boards/7#costas costas --contract team:tracker-update --part costas
hz report heartbeat costas-tracker                               # alive; silent for 2 days is offline
hz report sync                                                   # copy your logs into the checkout, then commit and push
hz graph                                                         # "Weekly report waiting on 2 updates", weeks, states
hz graph --heinzel costas-tracker --json                         # JSON Graph Format
```

A promise is `on_time`, `waiting_for_approval`, `late`, `fell_back` (kept on a stale input) or `offline`. The graph
covers the team only: entries are metadata, never content, and nothing is counted per person. Logs live in your state directory and in `telemetry/<handle>/<id>.jsonl` in the team repository, one writer each. Pushing stays yours.

`hz heinzel new keeper --template graph:team-graph-keeper` writes a contract that drafts a morning note from the graph.

## Your own state repository

hz notes, on your machine, the days you used it and each version it ran. Nothing leaves until you name
a private repository of your own. Then the daemon pushes new notes every 15 minutes to `hz/<handle>/<machine>`,
fast-forward only ([why](adr/0081-state-repositories-one-writer-per-branch-the-daemon-pushes.md)):

```bash
hz system config set system.state.personal https://github.com/mira-example/heinzel-state-mira   # private, yours
hz system state push                                             # now, rather than within 15 minutes
hz system state status                                           # where it goes, what is pending, the last push
```

The machine id is random, never the host name. A branch that moved to commits this machine did not make stops the push (`HZS001`); delete it if the commits are not yours, then `hz system state reset`.

## What waits for your yes

`new`, `join` and `add` show every step, with its command, before it happens. Reading, such as a clone, runs at once. Steps that change something outside your machine (creating a repository, pushing,
opening a pull request) and making a team yours wait for `--yes`.

An existing checkout from the deprecated `[heinzel] team_repository` becomes your team with `hz team adopt <path>`.

## Not built yet

- Automatic sync of reports to the team, and sharing state with the team.
