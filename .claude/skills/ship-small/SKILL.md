---
name: ship-small
description: Ship a small, low-risk change (a copy or docs fix, a small web UI tweak) from intent to a validated pull request with one command per stage, safe for a small model (Haiku). Use for typos, wording, doc fixes, link fixes and small component or style tweaks; anything else escalates by itself.
---

# Ship a small change

A script decides what is small and what is not; you never judge. Run the stages in order. Every command prints one
JSON line; do what its `next` field says, literally. `S` below is the `uv run --script .../scripts/ship.py` prefix that
`next` prints; always `cd` into your worktree first (your shell forgets it between commands).

| Stage | Command | Does |
| --- | --- | --- |
| 1 | `S start SLUG` | New worktree and branch (`SLUG`: 3-41 chars, lowercase, digits, dashes) |
| 2 | `S classify --files PATH...` | Before editing: is this file allowed? Exit 0 = edit it, exit 3 = not allowed |
| 3 | edit the files | Smallest edit that does the job. No new or deleted files |
| 4 | `S classify` | Reads your real diff: lane `low` or STOP |
| 5 | `S check` | Checks for exactly your files; fixes whitespace and formatting itself |
| 6 | `S pr "type(scope): summary" --body "why"` | Commits, pushes, opens the PR labelled `risk: low`, auto-merge off |
| 7 | `S wait` | Waits for CI; add `--budget 540` and a 600000 ms shell timeout to wait longer |
| 8 | `S verify` | After the orchestrator merged: Docs run green and the live page shows your text |

`S <stage> --help` prints a stage's usage, an example and the exit codes. Exit codes: **0** go on with `next`. **1** this stage failed: fix exactly what `detail` names, then repeat the stage.
**3** STOP: this is a correct outcome, not an error. Send the JSON line to whoever started you and run nothing else. **4** still running: run the same command
again. **2** you used the command wrongly: read `msg`.

Rules that no command can enforce:

- The second failure of any stage stops the change by itself (exit 3). Never retry a third time, never work around.
- Never merge, never touch auto-merge, never run `git push`, `git commit` or `make pr` yourself, never use
  `--no-verify`. `pr` does all of it. Merging belongs to the orchestrator.
- Never write a name of a person, employer, customer or private repository into a file. If `check` reports a
  confidential term, reword the text and never repeat the term anywhere.
- If the change turns out bigger than the job you were given, run `S escalate "one sentence why"` and stop.

The rules (paths, diff checks, limits) live in `scripts/ship.py`; background in [docs/SHIP_SMALL.md](../../../docs/SHIP_SMALL.md).
