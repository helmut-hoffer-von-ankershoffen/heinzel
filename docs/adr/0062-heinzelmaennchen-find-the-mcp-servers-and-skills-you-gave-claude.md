---
status: proposed
date: 2026-10-25
decision-makers: maintainer (direction), architect (shape)
related: 0019, 0027, 0028, 0030, 0045, 0050
---

# 0062: Heinzelmännchen find the MCP servers and skills you already gave Claude, read-only first

## Context and Problem Statement

A person who runs Heinzelmännchen has usually configured Claude already: MCP servers in Claude Desktop and Claude Code
(user, project and plugin scope), skills in `~/.claude/skills`, in projects and in plugins. Each took a setup step, a
login or a token. Heinzelmännchen turns ([ADR 0050](0050-pydantic-ai-runs-a-turn-a-coding-agent-is-a-handoff.md))
see none of it, and asking for every integration a second time is the opposite of "done, not nagged". The maintainer
asked that the MCP servers and skills of local Claude be available to the Heinzelmännchen and manageable in Settings.
Using them is a larger decision (grants, write confirmation, the engine); this record decides the part that comes first
and that every later part needs: finding them without leaking anything, and showing them.

## Decision Drivers

- Credentials never land in a file hz writes and never in output ([ADR 0030](0030-credentials-live-in-the-os-keychain.md)).
- Nothing is sent anywhere and nothing is switched on without the person ([ADR 0027](0027-nothing-is-sent-home.md),
  [ADR 0050](0050-pydantic-ai-runs-a-turn-a-coding-agent-is-a-handoff.md) §3).
- Server and skill names can reveal employers and customers; the unauthenticated REST head is reachable by any local
  web page ([ADR 0019](0019-mcp-and-rest-heads.md)).
- The files are the user's, written by another program: they can be malformed, huge, or contain links.

## Considered Options

- **Discover live from Claude's own files, list read-only, decide use later** (chosen): the source stays the truth for
  how to connect; hz will later keep only which items are enabled, keyed by a fingerprint.
- **Import into hz's configuration**: copies drift and the first thing copied is the token. Rejected.
- **Proxy through Claude Code**: reaches claude.ai connectors but costs a subscription run per use and brings Claude
  Code's permissions, not hz's. Kept as the later way to those connectors, not the main path.
- **Use servers and skills at once**: it needs the grant model, write-as-proposal and engine wiring, which deserve their
  own review. Not in this slice.

## Decision Outcome

Chosen option: **a submodule `system.claude` ("From Claude") that reads Claude's configuration and lists the servers
and skills it finds; it enables, connects to and runs nothing.** Checkable statements:

1. **Sources.** Claude Desktop (`claude_desktop_config.json`, extensions' `manifest.json`), Claude Code (`~/.claude.json`
   user and per-project `mcpServers`, `~/.claude/settings.json` `enabledPlugins`, the projects it lists with their
   `.mcp.json` and `.claude/skills`, enabled plugins' `.mcp.json` and `skills`), and `~/.claude/skills`. claude.ai
   connectors are only counted: they are reachable only through Claude Code. Not yet read: nested monorepo skills,
   Windows paths, Claude Code's per-tool permission hints.
2. **No secret value is read into a result.** The models have no field for one: an environment variable or header is
   listed by name, a URL by scheme and host, a command by its program without arguments (arguments may hold tokens).
   A token in a URL is named as such. The fingerprint hashes only that shape (program, argument count, host, names), so a
   changed argument is not noticed yet; a later slice that needs it hashes the full entry with a key kept on this
   machine. Extensions' keychain-backed values are never touched.
3. **Links stay inside.** Every file is resolved first and refused when it ends outside the directory it is expected in
   (a configuration file inside the home directory, a skill inside its skills directory, a plugin inside
   `~/.claude/plugins`, a project file inside the project), opened with `O_NOFOLLOW` as a regular file with a size
   limit; a named pipe is refused before it is opened, frontmatter with YAML aliases is not expanded. A refusal is listed
   with its reason; doctor reports unreadable, invalid and oversized files, and lists but does not complain about a
   refused link (a skill linked in from a dotfiles directory stays visible as skipped, and is not read). Bundled files
   are counted, never read or run. Of a plugin only the latest user-scope install of an enabled plugin is read.
4. **Third-party text is cleaned.** Skill names and descriptions pass `clean_text` and are cut.
5. **Heads.** `hz system claude list` (names) is for the CLI and the web head only. The MCP and REST heads get
   `hz system claude summary`: counts per source, no names. Doctor reports only whether every file could be read.
6. **Settings.** The web app has a read-only "From Claude" page under "This computer".
7. **Later slices** (not decided here): enabling and testing servers, per-tool read or write-with-confirmation, grants
   per Heinzelmännchen, skills in turns, OAuth for http servers, other clients' configuration.

### Consequences

- Good: the person sees what Claude knows on this computer in one place, before anything uses it; the discovery and
  its safety rules are tested once and shared by every later slice.
- Bad: a scan reads a few files per call (milliseconds); the list is local, so a person on a shared machine sees their
  own home only.
- Bad: names stay unavailable to MCP agents; an agent that needs them waits for an explicit setting in a later slice.

### Confirmation

`tests/unit/modules/claude_scan_test.py` (fake home directories: sources, no secret in any result, links, bad and large
files) and `tests/integration/claude_test.py` (CLI, web head with its token, and no names on the REST and MCP heads).
Link handling and head restrictions are otherwise checked in review.

## More Information

- The wider design (grants, write proposals, Pydantic AI toolsets, skills index, settings page with switches) is
  recorded as slices after this one and gets its own ADRs when they are built.
