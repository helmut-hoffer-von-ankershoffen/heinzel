# Join your team

For the engineer or product manager whose manager posted a line in chat. Run the line, answer what it asks, see your
team's first draft. Nothing is published, sent or scheduled unless you say so.

Examples use a made-up company, ACME, and its Checkout team.

## The one line from your manager

The line names your team's repository, which is the invite. You need [uv](https://docs.astral.sh/uv/)
(`brew install uv`), the [GitHub CLI](https://cli.github.com) signed in (`brew install gh`, then `gh auth login`) and,
for drafting, the [Claude Code](https://docs.claude.com/en/docs/claude-code) CLI signed in. Run the line as posted:

```bash
uvx heinzel@latest join https://github.com/acme-example/hz-team-checkout.git
```

The launcher installs the standard app when needed, then runs `join`. See the [installation guide](install.md) for
opening and stopping the workspace.

## What happens

`join` asks before anything leaves your machine, and does four things:

1. It says what hz keeps about you (below), before anything is written.
2. It finds out who you are from your `gh` and `gws` logins and this machine's clock, and asks only for the rest: your
   handle, your role and your working hours.
3. It clones the repository with your own access and looks you up in the roster by your GitHub login. When it finds
   you, it asks only "Make checkout your team?".
4. It sets up what is still missing, plugin by plugin, then runs `hz system doctor`.

You should see:

```text
hz keeps your profile and answers on this machine only, readable by you alone. ...
Joining checkout:
  done   clone https://github.com/acme-example/hz-team-checkout.git with your own access
  to do  make it your team in your user configuration ([team])
The roster lists you as theo-example (engineer).
Make checkout your team? [Y/n]: y

What happens next
  open hz             uvx heinzel@latest
  what is missing     hz setup, any time
  invite the next one post the same line: uvx heinzel@latest join https://github.com/acme-example/hz-team-checkout.git
```

**If the roster doesn't list you**, you still read the team's contracts. `join` lets you pick the roster entry that is
you (one without a login yet) or asks "Ask the owners of checkout to add you?", which opens a pull request an owner
merges. From a coding agent or without a terminal, every question has a flag:

```bash
hz join https://github.com/acme-example/hz-team-checkout.git --role engineer --yes --json
hz join https://github.com/acme-example/hz-team-checkout.git --propose --role product-manager   # not listed: open the PR
```

`join` is re-runnable: nothing is asked twice, and `hz setup` finishes whatever is missing.

## What hz keeps about you, and where

| Data | Where | Who sees it |
| --- | --- | --- |
| Handle, role, time zone, working hours, GitHub login, name, e-mail (your profile) | `[org]` in your user configuration file on this machine (`hz system config show` lists the files) | you |
| Your logins | in `gh`, `acli` and `gws`; hz stores no token and never asks for one | the vendor's CLI |
| Handle, GitHub login, optional e-mail, roles, since/until dates | `team/roster.yaml` in the team repository | everyone who can read the repository |
| Your preferences for the team | `people/<handle>.yaml` there; only you edit it (CODEOWNERS) | the same |
| Your name, title, manager, location | nowhere in Git | — |
| What you did, how much, how fast | nowhere; drafts are your own, about your own work | — |

Drafts stay on your machine under `<data dir>/workflow/drafts/` and are deleted after 90 days. Nothing is sent to the
project. `hz org show` shows your profile; `hz setup org` changes it.

## Your first win: this week's report

Your team's contracts are in the checkout's `workflows/`, as `team:<name>`:

```bash
hz heinzel list                              # your contracts and your team's, with schedule and problems
hz heinzel show team:weekly-report           # what it reads, what is bound, whether it is ready
hz heinzel wake team:weekly-report --dry-run  # the packet the helper would get: the week's pull requests and items
hz heinzel wake team:weekly-report            # draft it, within the cost cap; a local file, yours to edit or discard
hz heinzel drafts --name weekly-report --latest
```

`list` names the team's contracts ("No contracts yet." means your manager hasn't added one). `show` lists each
requirement with its bound adapter and, for an unmet one, the command that fixes it. `hz system doctor` says if the
agent runner (Claude Code by default) is missing.

## Daily use

```bash
hz system doctor                              # everything healthy? each problem with the command that fixes it
hz heinzel drafts                            # every draft, newest first
hz team show                                  # the team, its checkout and members
hz ui                                         # the same in a browser, on localhost, for this session only
hz system update                              # update in place, keeping plugins
```

A team contract runs on **your** machine only when you say so: `hz org sync --accept contract:weekly-report`, and the
daemon the [manager guide](manager.md#6-the-first-contract-this-weeks-report) describes. Every command takes `--json`.

## `hz org sync`: what your team recommends

Your team's `hz.yaml` may recommend plugins, bindings, credential names and contracts. It never changes your machine.

```bash
hz org sync                                   # every recommendation beside what you have; changes nothing
hz org sync --accept all                      # bindings and contracts you have no choice for yet
hz org sync --accept binding:documents        # one, by id
```

What `sync` shows and writes is in [TEAMS: what your team recommends](../TEAMS.md#what-your-team-recommends).

## Leave, or uninstall

```bash
hz system config unset team.checkout && hz system config unset team.repository   # leave: the team is no longer yours
hz system daemon uninstall                                                        # if you installed the daemon
hz system agent uninstall && uv tool uninstall heinzel                            # remove hz and its plugins
```

Your configuration files stay; `hz system config show` lists them. Leaving the roster is the owners' commit, and your
`people/<handle>.yaml` is yours to remove by pull request.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| "The roster of checkout doesn't list you yet." | Say yes to the pull request `join` offers (or `--propose`); an owner merges it. Listed without a login? Pick that entry, or `--handle <entry>` |
| `gh` isn't signed in, or as someone else | `gh auth login`, then run the line again; `hz team join <url> --login <your GitHub login>` says who you are without `gh` |
| The clone fails: no access | Ask the team's owner for read access |
| "You are in the team at ... already; one team for now" | Leave it first (above), then join |
| A checkout from `[heinzel] team_repository` | `hz team adopt <path>` makes it your team |
| "Not joined: nothing was changed" | You answered no, or ran without a terminal: run the line again and say yes, or pass `--yes` |
| `hz system doctor` is red | Each check names its fix; `hz setup` runs the steps still missing |

All commands: [CLI_REFERENCE](../CLI_REFERENCE.md); for your manager: [Bootstrap your team](manager.md). The
repository's layout: [TEAMS](../TEAMS.md).

## Not built yet

- Matching you in the roster by the e-mail of your Google login as well as your GitHub login.
- A draft of your own part of the week at the end of `join`.
