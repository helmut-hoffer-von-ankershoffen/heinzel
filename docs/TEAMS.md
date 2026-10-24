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

## Add someone, show, leave

- `hz team add theo --login theo-example --role engineer` adds someone to the roster, as an owner: a pull request, or a
  commit for a repository without a remote. `--by <your login>` says who you are when `gh` is signed in as someone else.
- `hz team show` lists the team, its checkout and its members, and what is wrong with them; `hz system doctor` checks
  the same.
- To leave: `hz system config unset team.checkout && hz system config unset team.repository`.

## What waits for your yes

`new`, `join` and `add` show every step, with the command it runs, before it happens. Reading (a clone into hz's data
directory) runs at once. Steps that change something outside your machine (creating a repository, pushing, opening a
pull request) and making a team yours wait for `--yes`. The commands are CLI and MCP only.

An existing checkout from the deprecated `[workflow] team_repository` becomes your team with `hz team adopt <path>`.
