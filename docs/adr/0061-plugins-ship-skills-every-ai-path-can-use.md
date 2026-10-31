---
status: proposed
date: 2026-10-24
decision-makers: maintainer (direction), architect and security reviewer (shape)
amends: 0014, 0050
amends-0014: plugin skills reach Claude Code through hz system skill export
amends-0050: skills are SkillLets; a turn gets an index and loads bodies on demand
related: 0019, 0037, 0040, 0044, 0045, 0047, 0049, 0057
implemented-by: 140
---

# 0061: Plugins ship skills every AI path can use: `module.skill(...)`, read-only tools, an explicit export

## Context and Problem Statement

Advice for *how* to do a piece of work is best written once, as an Agent Skill: a `SKILL.md` with a `name` and a
`description` in its frontmatter, a Markdown body and optional files beside it ([agentskills.io](https://agentskills.io),
[ADR 0014](0014-agent-skills-and-the-claude-code-plugin.md)). Today only the core ships skills, and only for Claude
Code (`agent-skills/`, `hz system agent install`). [ADR 0050](0050-pydantic-ai-runs-a-turn-a-coding-agent-is-a-handoff.md)
§7 planned plugin skills as a field of the playbook (`PlaybookLet.skills`) rendered whole into a turn's instructions.
The maintainer asked for more: plugins *and* the core contribute skills, and every AI path in hz can use them, Pydantic
AI turns, Claude Code and any MCP client, with per-Heinzelmännchen allowlists, one record type layered general to
specific, and scripts that never run in a turn. A skill's text is instructions by design, written by whoever wrote
the plugin, and it may be company-internal; so the question is also what keeps it from granting anything, leaking, or
running.

## Decision Drivers

- Data, not code, checked at import, the way themes are ([ADR 0044](0044-themes-are-data-a-plugin-ships.md)).
- A skill is advice: it can't add a tool, an input, a target or a cap (ADR 0050 §7, `HZC051`).
- Progressive disclosure: an index costs a line per skill; bodies and files are read when needed.
- One `Skill` record for every source, so the planned "From Claude" import (found Claude skills) feeds the same type.
- Nothing reaches another tool's configuration (`~/.claude/skills`) without the person deciding it.
- Skill names, descriptions and bodies may be internal: logs and telemetry carry names and fingerprints only.

## Considered Options

- **A `SkillLet`, `module.skill(path)`, consumed by `system.skill`** (chosen)
- **`PlaybookLet.skills`** (ADR 0050 §7): a playbook is one per module and is consumed by the workflow module (being
  renamed, [ADR 0051](0051-the-workflow-module-becomes-the-heinzel-module.md)); the core and modules without a playbook
  could not offer skills, and the skill catalog would depend on one consumer.
- **`skills/` directories found by convention, no let**: nothing checks them at import, and nothing ties a skill to the
  module and layer that ships it.
- **Export by rewriting the installed `agent-skills/` Claude Code plugin**: Claude Code owns its plugin cache and
  replaces it on every update; hz would write into another program's state.

## Decision Outcome

Chosen option: **a `SkillLet` that a module declares with `module.skill(path)`, checked at import and consumed by
`system.skill`, which resolves skills along the layer stack and serves them read-only to turns, MCP clients and, on an
explicit export, Claude Code**, because it reuses the theme pattern (data checked at import, ties decided by layer)
and keeps every grant in code.

1. **Format.** A skill is a directory `<name>/` with `SKILL.md`. Frontmatter (YAML, at most 4 KB, parsed safely):
   `name` (lowercase words joined by dashes, at most 64 characters, equal to the directory name, not starting with
   `hz` or `heinzel`), `description` (one line, at most 1,024 characters), and optionally the spec's and Claude Code's
   keys (`license`, `compatibility`, `metadata`, `allowed-tools`, `disallowed-tools`, `argument-hint`,
   `disable-model-invocation`, `user-invocable`, `model`), which hz keeps as *hints*: shown, never applied. Any other
   key is refused, keys starting with `hz` explicitly (`HZC051`: a skill can't grant). Files beside it are text only
   (strict UTF-8, no NUL), no symbolic links anywhere (dotfiles and `__pycache__` are ignored: never listed, read or
   exported), at most 64 files, 4 directories deep, 200 KB each and
   1 MB together; `SKILL.md` at most 64 KB. A file that looks like it holds a secret (a private key block or a common
   token prefix) refuses the skill, naming file and line, never the value; so does an invisible, control or
   bidirectional character (what `clean_text` would remove), so what `show` displays is what an export carries. A directory `skills/` holding several
   skills is declared with one call. All of this is checked when the plugin is imported, like a theme's fonts.
2. **Record.** `Skill` (kernel, public): name, description, body, hints, files (path, kind, size, sha256) and a
   fingerprint over the sorted, length-prefixed paths and contents, and the SHA-256 of `SKILL.md` itself. Kinds are `reference`, `script`, `asset`,
   `template` and `other`, from the top directory. `SkillLet` re-checks on construction, as `ThemeLet` does.
3. **Layers.** `system.skill` offers, by name, the most specific source, in this order from most to least specific:
   the personal directory (`[system.skill] personal_dir`, default `skills/` in the user configuration directory, read
   at listing time with the same checks, its problems reported, not fatal); the layers of the person's stack
   ([ADR 0040](0040-layers-and-playbooks-are-plugins.md)), the more specific first; the core; a plugin outside the
   stack. So a plugin outside the stack never shadows a core skill; two such plugins tie by module name. An org and
   the site offering one name with nothing more specific deciding is a conflict, left out and reported (ADR 0044's
   rule). Shadowed skills are listed with what hides them. The team repository's `skills/` and the found Claude
   skills are later sources of the same record; deferred.
4. **One read-only service.** The kernel declares `Skills` (implemented by `system.skill`): `index(allowed)`,
   `load(name, allowed)`, `read_file(name, path, allowed)` and `chat_allowed()`. `allowed` is enforced in the service, in code, never by
   the index text. `load` returns the body, cleaned (`clean_text`, `redact_emails`), cut at 24,000 characters and
   framed as data from its source, with the list of its files; `read_file` reads only a path the skill's record lists,
   opening every component below the skill's directory without following links, and refuses a file whose content
   changed since import, `SKILL.md` included. Each load returns the skill's source and fingerprint, so a turn can record what it read
   (ADR 0049).
5. **Pydantic AI turns.** The kernel ships `skill_tools(skills, allowed)`, two plain typed functions, `load_skill(name)`
   and `read_skill_file(name, path)`, and `skill_instructions(entries)`, the index (`name: description`, one line
   each, newlines stripped, at most 40 entries and 8,000 characters). The turn engine wraps them in a Pydantic AI
   `FunctionToolset`; the kernel and `system.skill` import no Pydantic AI. This replaces ADR 0050 §7's rendering of
   whole bodies: bodies are loaded on demand.
6. **Allowlists.** A contract names the skills its Heinzelmännchen may use in `hz.uses` (ADR 0050 §7); the engine
   passes them as `allowed`. The chat's set is `[system.skill] chat`, the core's skills by default. `personal_dir`,
   `chat` and `export_dirs` are ignored in a project `hz.toml`: a repository someone else controls adds no prompt text
   and picks no directory to write.
7. **MCP.** On the stdio MCP head (not on streamable HTTP, which has no authentication, and not in a chat's server),
   each skill is a prompt `skill_<name>` and each of its files, `SKILL.md` too, a resource `skill://<name>/<path>`,
   read through `Skills` when asked for.
8. **Claude Code.** `hz system skill export --name NAME` (repeated) or `--all-skills` (every skill but the
   person's own, which go only by name) writes the chosen skills into each of `[system.skill]
   export_dirs` (default `~/.claude/skills`) as `heinzel-<name>/`, a prefix no core skill of ADR 0014 uses. The
   exported `SKILL.md` keeps only `name` and `description` (Claude Code would honour `allowed-tools` and `model`, so
   hints are dropped), and files are copied from the bytes hz read and checked. A directory is written beside the
   target and moved into place, with mode 0700 and files 0600; hz replaces or removes only a directory it marked
   with `.heinzel-export` and that its manifest in the state directory lists; an unmarked or linked target is a
   reported conflict, never overwritten. After `hz system plugin add`, `develop` or `remove`, the new environment runs
   `hz system skill export --sync`, which only *removes*: exports whose skill is gone or whose fingerprint changed
   (shown as "changed: export again to review"). Nothing is ever added without the person naming it, since a skill in
   Claude Code can run its scripts. `hz system skill unexport` removes exports, never a directory hz didn't write.
9. **Scripts never run in a turn.** No tool runs a skill's script. A Heinzelmännchen that needs one hands the task to
   the coding agent (ADR 0050 §6), which gets the skill's directory in its private working directory; not wired until
   the engine exists.
10. **Surfaces and confidentiality.** `hz system skill list` and `show NAME` run on the CLI and the (paired) web head,
    not on the unauthenticated REST head and not as MCP tools, so a chat's agent reads no skill beyond its set; MCP
    clients get skills as the prompts and resources of (7). `export` and `unexport` run on the CLI only. Logs, doctor, info and telemetry
    name skills and fingerprints, never bodies or file contents; an error names the file, never its text.

### Consequences

- Good: one skill directory serves hz's own turns, Claude Code and every MCP client, and the same record will hold
  skills found in Claude.
- Good: a skill reaches a turn only by name from an allowlist, and Claude Code only by the person's export.
- Bad: a new let kind and a new prompt-injection surface: a plugin's skill text is instructions. It is bounded by the
  allowlist, cleaning, framing, caps and the shadowing rule, not removed; reviewing a plugin's skills is part of
  trusting the plugin.
- Bad: two writers to `~/.claude/skills` (`hz system agent install` for the core's Claude Code skills, `hz system
  skill export` for the rest), kept apart by the `heinzel-` prefix and the marker.
- Neutral: until the turn engine lands, `skill_tools` is ready and tested but attached by nothing but tests.

### Confirmation

`tests/unit/kernel/skills_test.py` (format, caps, hints, secrets, links, fingerprint, the re-check on read, a directory
swapped for a link, and the tools attached to a Pydantic AI `TestModel` turn as a `FunctionToolset`),
`tests/unit/modules/skill_service_test.py` (layer order, the core rule, org/site conflicts, the personal directory,
allowlists, export markers, modes, conflicts, sync and unexport), `tests/unit/heads/mcp_skills_test.py` (prompts and
resources on stdio only, none over HTTP or in a chat), `tests/unit/modules/plugin_service_test.py` (a plugin change
runs the sync and survives its failure) and `tests/integration/skill_test.py` (the CLI). The import-linter contracts
list `heinzel.kernel._skills` as private and `system.skill` as an independent submodule.

## More Information

- Amends [ADR 0014](0014-agent-skills-and-the-claude-code-plugin.md) (plugin skills reach Claude Code through `hz
  system skill export`, not the plugin) and [ADR 0050](0050-pydantic-ai-runs-a-turn-a-coding-agent-is-a-handoff.md) §7
  (skills come from `SkillLet`s, not `PlaybookLet.skills`; a turn gets an index and loads bodies on demand).
- Deferred: `hz.applies` (a skill loaded for every matching turn), the team repository's `skills/`, the found Claude
  skills, a web page for skills beyond the operations, and parsing `hz.uses` in the renamed contract module.
