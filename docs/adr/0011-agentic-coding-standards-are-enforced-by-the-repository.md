---
status: accepted
date: 2026-10-23
implementation: built
---

# 0011: Agentic coding standards are enforced by the repository

## Context and Problem Statement

Most changes to Heinzelmännchen will be written by coding agents. Standards that live only in a document are followed
only when someone remembers them.

## Considered Options

- **The repository enforces its standards on every contributor, human or agent** (chosen)
- **Standards that live only in a document**: followed only when someone remembers them.

## Decision Outcome

Chosen option: **enforce the standards in the repository**, because agents then get the same fast feedback as
people.

- **Instructions:** `AGENTS.md` (read by Codex, Cursor and others) is the single source; `CLAUDE.md` imports it.
- **Hooks** (`.claude/settings.json`): after every Python edit, ruff formats and lints the file and reports problems
  back to the agent; before an agent may finish, `make check` must pass.
- **Reviewer agents** (`.claude/agents/`): `critic`, `test-engineer`, `architect`, `security-reviewer`,
  `dx-reviewer`, `docs-reviewer`, `release-engineer` — one mandate each, read-only. The `review` skill runs only
  those whose area a change touches, in parallel, and merges findings into a go/no-go verdict; personas never call
  each other. We stop at seven: overlapping mandates add noise and cost, not quality.
- **Skills** (`.claude/skills/`) encode recurring procedures: adding a module or operation, writing tests and ADRs,
  reviewing, releasing, scaffolding a plugin.
- **Git hooks** (pre-commit): format, lint, types, conventional commit message.
- **CI** runs the same checks plus import-linter contracts, zizmor, pip-audit, CodeQL and the full test matrix; an
  optional Claude review workflow posts reviewer findings on pull requests when enabled.
- The plugin template carries all of the above, so plugins inherit it.

### Consequences

- Good: agents get fast, local feedback instead of failing CI later.
- Bad: the hooks need `uv` and `make` locally; they degrade to warnings when tools are missing.

### Confirmation

`tests/unit/repository_test.py`: `test_claude_settings_hooks_exist`,
`test_subagents_declare_name_description_tools_model_and_effort`, `test_skills_follow_the_agent_skills_specification`;
`.pre-commit-config.yaml`; the CI workflow `.github/workflows/ci.yml`; `make template-test` for the template.
