---
status: accepted
date: 2026-11-12
implementation: partial
decision-makers: maintainer (decision), architect (shape)
amends: 0062
amends-0062: found MCP servers are switched on by default; each can be switched off
related: 0019, 0027, 0028, 0030, 0045, 0050, 0068
---

# 0146: Found MCP servers are on by default, and each can be switched off

## Context and Problem Statement

[ADR 0062](0062-heinzelmaennchen-find-the-mcp-servers-and-skills-you-gave-claude.md) lists the MCP servers a person
already gave Claude and left open how one becomes usable by Heinzels. Its driver was "nothing is switched on without
the person", so every server would have needed a step before first use. The maintainer has now decided otherwise:
a person who configured a server for Claude wants their Heinzels to have it too, without being asked
again. This record decides the switch and its default. It is built before anything can use a server, so the default
is in place and visible first.

## Decision Drivers

- Done, not nagged: a server set up once should not need a second setup step.
- A server found in a project's files can come from a cloned repository, so "found" is not always "chosen".
- An agent must not be able to widen what it may use.
- Credentials never land in a file hz writes ([ADR 0030](0030-credentials-live-in-the-os-keychain.md)).

## Considered Options

- **On by default for every scope, each can be switched off** (chosen by the maintainer).
- **On by default for user scope and Claude Desktop, project and plugin scope listed but off**: the architect's
  recommendation, because a cloned repository could otherwise make a process available. The maintainer accepted that
  risk for a simpler rule, with the guardrails below.
- **Off by default** (ADR 0062's driver): safest, and asks for every server a second time. Reversed here.

## Decision Outcome

Chosen option: **every MCP server the scan finds is switched on by default, including project- and plugin-scoped
ones; the person can switch each one off, and it stays off.** Checkable statements:

1. **Default.** A found server has `enabled: true` unless a tombstone matches it. hz's own server (`status: self`) is
   never on. This reverses ADR 0062's driver "nothing is switched on without the person"; that ADR's other statements
   (no secret value in a result, links, cleaning, heads) stand.
2. **On means allowed, not running.** The switch records what Heinzels may use once a later slice can use servers.
   This slice starts, stops and connects to nothing.
3. **Scope is visible.** Each server carries `scope`: `user` (Claude Desktop and Claude Code's user entries),
   `project` (a project's `.mcp.json` or its local entries) or `plugin`. The CLI list and the Settings page show it
   next to the switch.
4. **Tombstones.** Switching a server off stores its id, name and shape fingerprint in `claude/servers.json` (mode
   0600, in a folder of mode 0700), and nothing that connects to it: no command, argument, URL, variable or header.
   A rescan matches by id first, so a changed argument, token or transport does not switch it back on, and neither
   does removing and re-adding the entry. It matches by name and fingerprint second, so the same server in a moved
   project folder or another scope stays off. **A switch changes one server and no other.** Switching a
   server off always stores a tombstone with its own id, also when another entry's tombstone already covers it.
   Switching one on removes its own tombstone and those of the same name and shape whose entries are gone. Whatever
   either would change for another server that is found is undone for that server: one that was off gets its own
   tombstone, one that was on is recorded as allowed past the tombstone that now covers it. So a tombstone's reach
   by name and shape is for entries that appear later, such as a second clone of a repository. An allowed id is kept
   while its entry is missing from a scan (an unmounted project) and dropped only when that id is switched off.
   **Renaming an entry switches it back on** when nothing else about it identifies it: the id and the name both
   change, and a shape alone is shared by unrelated servers, so matching on it would switch off servers nobody
   chose. The renamed server is new to the notice (statement 8).
5. **State that no setting can move.** The tombstones are not configuration: `hz system config set` is offered over
   MCP and a project's `hz.toml` comes with a repository, so either could remove a tombstone kept as a setting. For
   the same reason the file follows neither the `state_dir` setting, which both can change, nor `HZ_STATE_DIR`,
   which an installed daemon is started with from that setting. It lives in the platform's default state folder of
   the person's home directory, so the CLI, the web app and the daemon read the same file whatever the settings
   say. `HZ_CLAUDE_SWITCH_DIR` moves the folder for tests and isolated instances; no setting feeds it and the
   daemon's profile never carries it. None of this module's operations on the MCP and REST heads writes the file.
   The file never grows past what hz reads back: a write that would
   exceed the 1 MiB limit is refused and changes nothing, and ids of servers that are no longer found leave the
   notice's list when it is next closed.
6. **Heads.** `hz system claude enable|disable <id>` and `acknowledge` exist on the CLI and the web head (behind its
   token) only. The web head takes the id in the request's body, not its address. The MCP and REST heads get none
   of the three; their summary gains the count of switched-on servers.
7. **Fail closed.** A state file that is unreadable, invalid, too large, a link or of an unknown version switches
   every server off, is reported by doctor, and is not overwritten; the person moves it away to start again.
8. **First-run notice.** While switched-on servers exist that the person has not been told about, the list (CLI and
   Settings page) says how many are on and how many come from projects and plugins, and how to switch one off.
   `acknowledge` closes it for the servers found then (the Settings page: for the servers it showed); a server found
   later, such as one a cloned repository brings, shows it again.
9. **Logging.** Switching and acknowledging each log one start and one outcome event with the new state and the
   scope, never the id or name (they can name employers, customers and project folders); a refusal's message, which
   is logged, does not repeat the id either.

Guardrails the maintainer's decision relies on, for the slices that make servers usable (planned, not built here):

- **Lazy start.** A stdio server is started on first use and stopped when idle; default-on starts nothing at boot or
  on a scan.
- **Writes are confirmed.** Reading is silent; every write or send goes through the existing confirmation
  ([ADR 0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md), [ADR 0068](0068-human-approval-proofs-and-head-parity.md)). A tool
  of unknown kind counts as a write. A person may later choose "always allow" for one kind of write tool.
- **Secrets are never copied.** How to start a server is re-read from Claude's file when it is needed and held in
  memory only; the source file stays the truth.

### Consequences

- Good: a server set up for Claude is available to Heinzels without a second step; switching one off is one click
  or one command and survives rescans.
- Bad, accepted: opening a cloned repository in Claude Code can add a project-scoped server that is on for Heinzels
  before the person has looked at it. Until servers can be used this has no effect; after that, the notice, the visible
  scope, lazy start and confirmed writes are what stands between the repository and the person's data.
- Bad: the fingerprint hashes shape, not arguments (ADR 0062 §2), so two servers of the same name and shape in two
  projects share a tombstone. It errs towards off.
- Bad: a person with a shell can edit the state file or set `HZ_CLAUDE_SWITCH_DIR`; so can an agent that was given a
  shell.
  The head restriction covers hz's own operations only.
- Bad: a person who moved hz's state with the `state_dir` setting finds this one file in the default place.

### Confirmation

`tests/unit/modules/claude_switch_test.py` (default-on, scope, tombstones across changed, removed, moved and renamed
entries, the state file's content, mode, place and size limit, fail-closed, the notice, log events) and
`tests/integration/claude_test.py` (CLI, web head behind its token, absent from REST and MCP, and a `state_dir`
set in a project file or with `hz system config set` moving nothing, also for a daemon installed afterwards). A
property test runs sequences of switching, moving, renaming, adding and removing and checks that a switch never
changes another server. `web/src/layout/FromClaudeSection.test.tsx` covers the switch
and the notice on the Settings page.

## More Information

- Later slices: the seam that hands a module a server's start instructions, the Smart Connector plugin
  (`heinzel-smart-connectors`) that maps a server's tools to calendar, mail and documents, "always allow" for a kind
  of write tool, per-Heinzel narrowing, more clients' configuration files. Local servers come first.
- The commands stay under `hz system claude` while Claude's files are the only source; a client-neutral name is for
  the slice that reads other clients.
