# Shipping small changes

A copy fix, a docs correction or a small web UI tweak should reach production without a large model. `scripts/ship.py`
is the golden path for that: one command per stage, one JSON line of output, the next step spelled out, and a script
(not the model) deciding what counts as small. The short version for a model is the skill
[`ship-small`](../.claude/skills/ship-small/SKILL.md); this page is for the people who maintain the rules.

## The stages

| Command | What it does |
| --- | --- |
| `start SLUG` | A worktree `../heinzel-SLUG` on branch `docs/SLUG` from `origin/main` (`--kind fix\|feat\|chore`), dependencies installed |
| `classify --files PATH...` | Before editing: may this file be a small change? |
| `classify` | Reads the real diff: lane `low`, or STOP |
| `check` | Scoped checks for the changed files only, then remembers the content it vouched for |
| `pr "TITLE" --body WHY` | Commit, `hz-gate make pr` (draft, so auto-merge never arms), label `risk: low`, ready, auto-merge verified off |
| `wait` | Waits for `CI OK` at the head; merges `main` in when behind; reruns a known flaky job once |
| `verify` | After the merge: the Docs run of the merge commit is green and the published page shows the new text |
| `merge PR` | Orchestrator only (`SHIP_ORCHESTRATOR=1`): re-classifies the PR from GitHub's data, then `hz-merge-train` |
| `status`, `escalate "WHY"`, `reset` | Where am I; stop and hand over; the orchestrator clears a stopped change |

Exit codes: 0 go on with `next`; 1 the stage failed; 2 wrong usage; 3 STOP; 4 still running (run it again). `check`
and `pr` run as detached jobs, so a shell timeout cannot kill a push half way; each call waits at most 100 seconds.

## What is small

Decided by `scripts/ship.py`, from paths first and the diff second. Lane `low` needs all of:

- Every file is on the allowlist: `docs/*.md`, `docs/guides`, `docs/research`, `README.md` (with its derived
  `template/README.md.jinja`), `docs/overrides/*.html`, and web components, pages, layout, splash and `index.css`.
- No file is on the escalation list: kernel and all Python source, tests, `.github`, `.claude`, scripts, template,
  desktop, build and dependency files, root documents the maintainers own, ADRs, generated documents, documents about
  security, authority or publishing. Code paths that mention auth, approvals, permissions, tokens, sessions,
  durable agents, installer, updates, release, security or sandboxes escalate by name.
- Nothing is added, deleted, renamed or binary.
- In Markdown: no code block touched, no inline code or external link added or removed. Relative links must resolve.
- In web code: no network, storage, `innerHTML`, script, credential, URL or new import.
- At most 8 files and 80 changed lines.

Anything else stops the change with the reasons; the orchestrator picks a bigger model. The rules are the tables at
the top of the script and are tested in `tests/unit/ship_test.py`; loosen them with data, not with judgement.

## Failure budget

A failed stage prints what went wrong and the literal next step. The second failure of a change, or any classification
as not small, writes `stopped` into the worktree's git directory: every later stage refuses with exit 3 and prints a
report (directory, branch, PR, failures) for the orchestrator. `SHIP_ORCHESTRATOR=1 ... reset` clears it.

## Relation to the rest of the pipeline

- Heavy commands queue behind `hz-gate`; `make pr` runs with `NO_PREFLIGHT=1` and hosted CI does the rest.
- `pr` opens a draft, labels it `risk: low`, marks it ready and verifies auto-merge is off. The review-policy work on
  `make pr` and the release workflow stays authoritative: if `make pr` stops arming auto-merge itself, `pr` simply
  finds nothing to disarm. The label is the hook for risk tiers in review.
- Merging stays a separate decision. `merge` only accepts a PR whose label and whole diff both say low, and then uses
  `hz-merge-train` (update, wait for `CI OK` at the exact head, squash). Until the orchestrator trusts the lane, it
  merges by hand.
- Releases, installer, approvals, durable sessions and the kernel never use this path.
