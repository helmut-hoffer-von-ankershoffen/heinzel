# CLAUDE.md

@AGENTS.md

## Claude Code

Plan, delegate, verify, report outcomes. Ask the user only for product direction, anything irreversible or
outward-facing (publishing, deleting, force-pushing, spending), credentials, and conflicts between their instructions.
Never ask them to pick models, agents or tools.

Route work to the cheapest tier that does it well, cheap to expensive:

| Work | Who |
| --- | --- |
| Anything a make target or script does | the tool, not a model |
| Why CI or a check failed | `ci-triage` |
| Sweeping searches across many files | built-in `Explore` |
| Tests, CLI/DX, docs, CI/CD review | `test-engineer`, `dx-reviewer`, `docs-reviewer`, `release-engineer` |
| Correctness and security review | `critic`, `security-reviewer` (skill `review` picks the reviewers) |
| Design decisions, ADRs, structural review | `architect`; a whole area before a release: skill `raise-the-bar` |
| Whole-repo consistency audit, weekly or before a release | `consistency-auditor`; the loop: skill `self-audit` |
| What did the maintainer have to point out | `correction-miner` |
| Implementation | this session |

Keep each agent's frontmatter model and effort unless a skill says otherwise.

Run independent pieces (no shared results or files) concurrently in background subagents; keep the piece that needs
this conversation. Run research, measurement and review alongside implementation. A subagent that edits the repository
gets its own worktree unless it is the only writer. Check each result before building on it.

Hooks (`.claude/settings.json`) lint the Python you write, block `--no-verify`, `--force`, pull requests not
targeting `main` and checks piped into a push, and refuse to end a turn while `make check-fast` fails. Fix the cause;
never work around a hook.
