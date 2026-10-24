---
status: accepted
date: 2026-10-23
amended-by: 0060
---

# 0014: Agent skills ship with Heinzelmännchen and as a Claude Code plugin

## Context and Problem Statement

Everyone onboards the same way, ideally by asking their coding agent. That needs skills that install, use and
remove Heinzelmännchen, available before Heinzelmännchen is installed, and testable.

## Considered Options

- **Agent Skills with bundled scripts, shipped in the wheel and as a Claude Code plugin** (chosen)
- **Skills with free-form instructions**: the end-to-end tests could not run exactly what an agent runs.

No other alternatives were recorded when this decision was made.

## Decision Outcome

Chosen option: **Agent Skills that ship with Heinzelmännchen and as a Claude Code plugin**, because they work before
Heinzelmännchen is installed and are tested end to end.

- Heinzelmännchen's user-facing skills (`hz-init`, `hz`, `hz-uninstall`) follow the
  [Agent Skills](https://agentskills.io) format and live in `agent-skills/skills/`.
- This repository is also a Claude Code plugin marketplace (`.claude-plugin/marketplace.json`) with one plugin,
  `agent-skills`, so `/plugin marketplace add …`, `/plugin install heinzel@heinzel`, `/hz-init` works without
  Heinzelmännchen installed.
- The skills are packaged into the wheel; `hz system agent install` / `uninstall` copy them into each directory in
  `[system.agent] skill_dirs` (default `~/.claude/skills`), for agents without plugin support.
- Skills run scripts that ship with them (`scripts/install.sh`, `scripts/uninstall.sh`) rather than free-form
  instructions, so the end-to-end tests run exactly what an agent runs.
- Contributor-facing skills, agents and hooks stay in `.claude/` and are not part of the plugin.

### Consequences

- Good: the install and uninstall lifecycle through skills is tested end to end in CI on macOS and Linux.
- Good: frontmatter of every skill is validated by a unit test against the Agent Skills rules.

### Confirmation

`tests/unit/repository_test.py` (`test_skills_follow_the_agent_skills_specification`,
`test_claude_plugin_marketplace_points_at_the_plugin`), `tests/unit/modules/agent_service_test.py` and
`tests/e2e/tool_install_test.py::test_skill_lifecycle`.
