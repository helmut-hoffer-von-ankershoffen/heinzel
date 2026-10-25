# CLAUDE.md

@AGENTS.md

## Claude Code

**You direct; the user decides.** Plan, delegate, verify and report outcomes. The user states intent and never has
to pick models, agents or tools; ask them only for product direction, anything irreversible or outward-facing
(publishing, deleting, force-pushing, spending), credentials, and conflicts between their own instructions.

**Route work to the cheapest tier that does it well**, from cheap to expensive:

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

Each agent's model and effort are set in its frontmatter; don't override them unless a skill says to.

**When implementation is delegated to subagents, pick model and effort by the feature's complexity.** Sonnet, low
effort: renames, copy, small UI tweaks, config, docs-only. Sonnet, medium: most features. Escalate to Sonnet, high,
only when an agent hits problems (repeated CI/test failures, a stuck rebase, review findings it can't resolve), and
beyond that to Opus when it gets hairy: very complex work or problems Sonnet high can't resolve. Opus from the start
only for hard, cross-cutting work (turn engine, durable sessions, state sync, wake loop and triggers, security-sensitive
or ADR-level-risk code). Fable for design and research. Reviewers and `ci-triage` keep their frontmatter models; CI
log reading goes to `ci-triage`.

**Parallel by default.** Before multi-part work, split it into pieces that neither depend on each other's results
nor touch the same files, and run them concurrently in background subagents; keep the piece that needs this
conversation. Research, measurement and review run alongside implementation. Never two writers on the same files:
a subagent that edits the repository gets its own worktree unless it is the only writer. Check every subagent's
result before building on it.

**Hooks** (`.claude/settings.json`) lint the Python you write, block `--no-verify`, `--force`, pull requests not
targeting `main` and checks piped into a push, and refuse to end a turn while `make check-fast` fails. Fix the cause;
never work around a hook.
