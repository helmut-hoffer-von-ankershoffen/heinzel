# CLAUDE.md

@AGENTS.md

## Claude Code

Hooks (`.claude/settings.json`) lint the Python you write, block `--no-verify`, `--force`, pull requests not
targeting `main` and checks piped into a push, and refuse to end a turn while `make check-fast` fails; fix the cause,
never work around a hook. Reviewer subagents live in `.claude/agents/` with their model and effort in the
frontmatter; the `review` skill runs the ones a change touches, in parallel, and merges their verdicts. The
`self-audit` skill audits the whole repository for drift with `consistency-auditor` and `correction-miner`.
