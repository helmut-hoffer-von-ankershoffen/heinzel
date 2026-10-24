---
name: codify
description: Turn a standard the user sets, a correction they make or an instruction they repeat into the strongest durable enforcement - a test or CI check, a lint rule, a git hook, a Claude Code hook, a skill or agent instruction, an AGENTS.md rule, or (last) a memory - and retrofit what already violates it. Use whenever the user corrects how something is done, says "always" or "never", or repeats an instruction.
---

# Codify

An intervention the user has to make twice is a missing check. Make it durable at the strongest level that can
express it, prove that level catches a violation, and fix the existing violations.

## Choose the level (strongest first)

| Level | Use when | Where |
| --- | --- | --- |
| Test or CI check | The rule is checkable from files: structure, presence, naming, links, frontmatter, AST patterns | `tests/unit/*_test.py` (AST and file checks like `standards_test.py`), CI job |
| Lint or type rule | An existing tool has a rule for it | `pyproject.toml` (ruff, mypy, pyright, import-linter), `typos.toml`, zizmor |
| Git hook | It must hold for every tool and person before code is shared | `.pre-commit-config.yaml` (prek) |
| Claude Code hook | It is about how an agent works (commands, edits, finishing) | `.claude/hooks/`, `.claude/settings.json` |
| Skill or agent | It is a procedure or a judgment call | `.claude/skills/`, `.claude/agents/` |
| AGENTS.md / CLAUDE.md | It is a working agreement no tool can check | one line, where the topic already lives |
| Memory | It is about the user personally, not the repository | Claude Code memory |

Prefer one strong check over several weak reminders; don't also write a prose rule for what a test enforces.

## Steps

1. State the standard in one sentence, as the user meant it. If it is ambiguous, ask once.
2. Pick the level from the table and implement it. Plugins inherit the template: put shared checks where
   `scripts/sync_template.py` or the template symlinks carry them.
3. Show it works: introduce a violation, watch the check fail, remove it.
4. Fix the existing violations in the same change.
5. Commit with a conventional message whose body says what was codified and at which level.
