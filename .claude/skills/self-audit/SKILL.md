---
name: self-audit
description: The self-improving consistency loop - mine what the maintainer had to point out, audit the whole repository for drift between sources of truth, propose each finding as a failing check (draft pull request) or an issue, measure precision and escapes in a public ledger, and, rarely, improve the two auditing agents against recorded cases. Arguments tier=mine|weekly|deep (default weekly) and dry-run. Use from a scheduled task, before a release, or when asked to audit the repository.
---

# Self-audit

The loop finds what would otherwise need the maintainer to point it out. It proposes; the maintainer decides. It
never merges, never marks a pull request ready, never pushes to `main` and never edits its own guardrails.

**Arguments.** `tier=mine` (nightly: step 1 only), `tier=weekly` (default: steps 2–6 and 8), `tier=deep` (monthly:
steps 1–8). `dry-run` runs every step but 5 and 7, writes nothing outside the report and appends nothing to the ledger.

**Budgets.** mine: model haiku, at most 20 turns. weekly: at most 60 turns and 30 minutes. deep: at most 120 turns
and 60 minutes. Stop at the budget and report what is unfinished.

**State.** `.claude/audit/ledger.jsonl` is append-only, one JSON object per line, with a `kind` of `correction`,
`finding`, `outcome`, `metrics` or `self-edit`. The repository is public: the ledger holds fingerprints, classes and
paraphrases of at most 15 words, never quotes, names or transcript paths. Create it empty if it is missing.
`.claude/audit/inbox/` is git-ignored; the SessionEnd hook `queue_transcript.py` appends transcript paths to its
`queue`.

## Steps

1. **Mine** (tiers mine and deep). Start `correction-miner`. Append its lines to the ledger, then empty
   `.claude/audit/inbox/queue`. Tier mine stops here and reports counts per class and recurrences.
2. **Record outcomes.** `gh issue list --label audit --state all --json number,state,title,body,closedAt` and the
   same for `gh pr list`. Append an `outcome` line per proposal decided since the last run: merged = `accepted`,
   closed unmerged = `rejected` with the closing comment paraphrased as the reason, open more than 14 days = `stale`.
3. **Audit.** Start `consistency-auditor` with the ledger summary from steps 1–2. On tier deep, start it with model
   `fable` and effort `max`, and ask it to also compare I8 with two or three well-regarded peer projects, as skill
   `raise-the-bar` does.
4. **Select.** Drop findings whose fingerprint is open in the ledger, whose class was rejected twice, that are
   Unconfirmed, or that match a `do_not_codify` correction. Keep at most five; at most two become pull requests.
5. **Propose** (skipped on dry-run). Titles and bodies pass `uv run --script scripts/check_confidentiality.py --text`.
   - A finding with a failing check: in a fresh worktree from `origin/main`, apply skill `codify` (a plugin without
     it adds the check under `tests/unit/`), fix the violations, then `DRAFT=1 make pr TITLE="test(dx): ..."`. Label
     it `audit` and put the fingerprint in the body. Never enable auto-merge, never mark it ready.
   - Any other finding: one issue labelled `audit` with the fingerprint, the evidence and the proposed check.
   - Append a `finding` line per proposal: fingerprint, class, `pr` or `issue` and its number.
6. **Measure.** Over the last 90 days: precision = accepted / (accepted + rejected); escapes = new A–F corrections
   whose location no earlier finding named; recurrences = corrections marked `recurrence`; cost = turns and minutes
   of this run. Append one `metrics` line.
7. **Improve itself** (tier deep only, at most once a month, and only after at least ten decided proposals since the
   last `self-edit` line). Propose one change of at most 30 lines to `.claude/agents/consistency-auditor.md` or
   `.claude/agents/correction-miner.md`, aimed at the escapes and rejections. Score current and proposed prompts
   against every case in `.claude/audit/evals.yaml` (skip this step when the file is missing): for each case, a
   worktree at its `commit`, the agent on model `sonnet`, a hit when it reports the expected class at the expected
   location. Open a draft pull request only if the proposal misses no case the current prompt catches and catches at
   least one more; append a `self-edit` line either way. Never edit this skill, the budgets, the guardrails, the
   safety or confidentiality lines of the agents, or any test.
8. **Report.** Findings kept and dropped (with the reason), proposals opened, outcomes recorded, the metrics line,
   and anything unfinished. On dry-run, the proposals that would have been opened.

## Scheduling

The maintainer schedules the loop as local desktop scheduled tasks in this repository, not CI (transcripts are
local): nightly `/self-audit tier=mine`, weekly on Monday `/self-audit tier=weekly`, and monthly on the first Monday
`/self-audit tier=deep`.
