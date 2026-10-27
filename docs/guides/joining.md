# Join your team

For the engineer or product manager whose manager posted a line in chat. Ten minutes: run the line, answer what it
asks, see your team's first draft. Nothing is published, sent or scheduled unless you say so; what hz keeps about you
is listed below.

Examples use a made-up company, Acme (`acme-example` on GitHub) and its Checkout team. What is not in this release is
marked **coming in the next release**, with what to do meanwhile.

## The one line from your manager

The line names your team's repository; the repository is the invite, and the code host's permissions decide who may
read it. You need [uv](https://docs.astral.sh/uv/) (`brew install uv`), the [GitHub CLI](https://cli.github.com)
signed in (`brew install gh`, then `gh auth login`) and, for drafting, the
[Claude Code](https://docs.claude.com/en/docs/claude-code) CLI signed in (the default agent runner). Then run the line
as posted:

```bash
uvx --from heinzel heinzel join https://github.com/acme-example/hz-team-checkout.git
```

This PyPI command runs hz without a persistent installation. The invite printed by `hz join` currently uses the Git
repository at your release tag (`@vX.Y.Z`). To keep hz, use the recommended PyPI installation once and run
`hz` from then on:

```bash
uv tool install heinzel
hz join https://github.com/acme-example/hz-team-checkout.git
```

## What happens

`join` does four things, and asks before anything leaves your machine ([ADR 0032](../adr/0032-modules-declare-interactive-setup-steps.md)):

1. **Says what hz keeps about you** (below), before anything is written.
2. **Finds out who you are.** It looks up your `gh` and `gws` logins and this machine's clock, shows what it found and
   asks only for the rest: your handle (your GitHub login is a good one), your role and your working hours. This is
   your profile, kept on this machine.
3. **Joins the team.** It clones the repository with your own access (into hz's data directory, under `team/`) and
   looks you up in the roster by your GitHub login. It asks nothing when it finds you, only "Make checkout your team?".
   Matching by the e-mail of your Google login as well is **coming in the next release**.
4. **Sets up what is still missing**: each plugin's step, which you may skip, then `hz system doctor`.

What you should see:

```text
hz keeps your profile and answers on this machine only, readable by you alone. Joining a team puts your handle,
GitHub login and role in its roster; your name and email stay off every repository, and hz records nothing about
how much you do.
...
Joining checkout:
  done   clone https://github.com/acme-example/hz-team-checkout.git with your own access
  to do  make it your team in your user configuration ([team])
The roster lists you as theo-example (engineer).
Make checkout your team? [Y/n]: y

What happens next
  keep hz             uv tool install git+https://github.com/helmut-hoffer-von-ankershoffen/heinzel
  what is missing     hz setup, any time
  invite the next one post the same line: uvx --from ... heinzel join https://github.com/acme-example/hz-team-checkout.git
```

**If the roster doesn't list you**, you still read the team's contracts, and one question remains: who are you?
`join` lets you pick the roster entry that is you (an entry without a login yet) or asks "Ask the owners of checkout
to add you? This opens a pull request from your account." The proposal is never a fact: your manager, an owner,
merges it. From a coding agent or without a terminal, every question has a flag:

```bash
hz join https://github.com/acme-example/hz-team-checkout.git --role engineer --yes --json
hz join https://github.com/acme-example/hz-team-checkout.git --propose --role product-manager   # not listed: open the pull request
hz join https://github.com/acme-example/hz-team-checkout.git --answers answers.toml             # your team's preset answers; never secrets
```

`join` is re-runnable: nothing is asked twice, and `hz setup` is what you run any time for whatever is still missing.

## What hz keeps about you, and where

| Data | Where | Who sees it |
| --- | --- | --- |
| Handle, role, time zone, working hours, GitHub login, name, e-mail (your profile) | `[org]` in your user configuration file on this machine (`hz system config show` lists the files) | you |
| Your logins | in `gh`, `acli` and `gws`; hz stores no token and never asks for one | the vendor's CLI |
| Handle, GitHub login, optional e-mail, roles, since/until dates | `team/roster.yaml` in the team repository | everyone who can read the repository |
| Your preferences for the team | `people/<handle>.yaml` there; only you edit it (CODEOWNERS) | the same |
| Your name, title, manager, location | nowhere in Git | — |
| What you did, how much, how fast | nowhere; drafts are your own, about your own work | — |

Drafts and their records stay on your machine under `<data dir>/workflow/drafts/` and are deleted after 90 days;
nothing is sent to the project ([ADR 0027](../adr/0027-nothing-is-sent-home.md),
[ADR 0024](../adr/0024-people-and-org.md)). `hz org show` shows your profile; `hz setup org` changes it.

## Your first win: this week's report

Your team's contracts are in the checkout's `workflows/`, as `team:<name>`; your manager put the weekly report there
([Bootstrap your team, step 8](manager.md#8-drafts-approval-and-the-success-measure)):

```bash
hz heinzel list                              # your contracts and your team's, with schedule and problems
hz heinzel show team:weekly-report           # what it reads, what is bound, whether it is ready
hz heinzel wake team:weekly-report --dry-run  # the packet the agent would get: the week's pull requests and items
hz heinzel wake team:weekly-report            # draft it, within the cost cap; a local file, yours to edit or discard
hz heinzel drafts --name weekly-report --latest
```

What you should see: `list` names the team's contracts ("No contracts yet." means your manager hasn't added one);
`show` lists each requirement (`code-host:read`, `tracker:read`, …) with the adapter bound to it and, for an unmet
one, the command that fixes it; `run` reports drafting time, cost and the draft's path. The agent runner (Claude Code
by default) must be installed; `hz system doctor` says so otherwise.

A draft of your own part of the week, offered at the end of `join` from the team's playbook, is
**coming in the next release**.

## Daily use

```bash
hz system doctor                              # everything healthy? each problem with the command that fixes it
hz heinzel drafts                            # every draft, newest first
hz heinzel show team:weekly-report           # is it ready, when does it run, what to run next
hz org show                                   # your profile
hz team show                                  # the team, its checkout and members
hz ui                                         # the same in a browser, on localhost, for this session only
hz system update                              # a newer release? update in place, keeping plugins
```

A team contract runs on **your** machine only when you say so: `hz org sync --accept contract:weekly-report`
(below), and the daemon your manager's guide describes. Every command takes `--json`.

## `hz org sync`: what your team recommends

A team's `hz.yaml` may recommend plugins, capability bindings, credential names and contracts. It only recommends:
nothing in the repository installs, binds, stores or schedules anything on your machine.

```bash
hz org sync                                   # every recommendation beside what you have; changes nothing
hz org sync --accept all                      # bindings and contracts you have no choice for yet
hz org sync --accept binding:documents        # one, by id; what you chose otherwise switches only when you name it
```

What you should see: a table "Recommended by …" (Id, Recommended, You have, State, What it does), then the next
steps. Plugins and credentials stay commands you run yourself, which `sync` names; `hz system doctor` says when
recommendations wait for you. Details: [TEAMS: what your team recommends](../TEAMS.md#what-your-team-recommends).

## Leave, or uninstall

```bash
hz system config unset team.checkout && hz system config unset team.repository   # leave: the team is no longer yours
hz system daemon uninstall                                                        # if you installed the daemon
hz system agent uninstall && uv tool uninstall heinzel                            # remove hz and its plugins
```

Your configuration files stay; `hz system config show` lists them before you uninstall. Leaving the roster is the
owners' commit (it sets `until`; handles are never reused), and your `people/<handle>.yaml` is yours to remove by
pull request.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| "The roster of checkout doesn't list you yet." | Say yes to the pull request `join` offers (or `--propose`); an owner merges it. Listed without a login? Pick that entry when `join` asks, or `--handle <entry>` |
| `gh` isn't signed in, or as someone else | `gh auth login`, then run the line again; `hz team join <url> --login <your GitHub login>` says who you are without `gh` |
| The clone fails: no access to the repository | Ask the team's owner for read access on the code host; hz adds no permission of its own |
| "You are in the team at … already; one team for now" | One team at a time: `hz team show` names it; leave it first (above), then join |
| A checkout from `[heinzel] team_repository` | `hz team adopt <path>` makes it your team; `hz system doctor` says so until you do |
| "Not joined: nothing was changed" | You answered no, or ran without a terminal: run the line again and say yes, or pass `--yes` |
| `hz system doctor` is red | Each check names its fix, as a command to copy; `hz setup` runs the steps still missing |
| A team contract isn't ready | `hz heinzel show team:<name>` lists the unmet requirement and the command for it |

All commands: [CLI_REFERENCE](../CLI_REFERENCE.md); for your manager: [Bootstrap your team](manager.md).
