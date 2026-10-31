---
name: write-adr
description: Record an architecture decision for Heinzelmännchen as an ADR in docs/adr. Use before a structural change (new module kind, let kind, head, dependency, process change), when a change contradicts an existing ADR, or when asked to document a decision.
---

# Write an ADR

The format is a trimmed [MADR 4](https://adr.github.io/madr/); `docs/adr/0000-template.md` is the source of truth.

1. Read `docs/adr/README.md` and the ADRs related to the decision; ask the `architect` agent if unsure.
2. Copy `docs/adr/0000-template.md` to `docs/adr/NNNN-title-in-kebab-case.md` with the next number; the title is
   `# NNNN: Title that states the decision`. Delete the template's comment.
3. Front matter, flat `key: value` only: `status: proposed`, `date: YYYY-MM-DD`, optional `decision-makers` (roles,
   never names), `supersedes` / `amends` when the decision replaces or changes another ADR, and `related` for ADRs
   it builds on without changing them. For each ADR it amends or supersedes, add `amends-NNNN: what it changes` (a few
   words, no `: ` or ` #` inside); the index and both pages show it. Once a pull request builds the decision, add
   `implemented-by: 123, 145` (PR numbers of the code, never the PR that only wrote the record); leave it out until then.
4. Fill in, keeping it to a page or two:
   - Context and Problem Statement: forces and verified facts, sources linked.
   - Decision Drivers (optional).
   - Considered Options: the chosen one marked `(chosen)`, each other one with why not. Never invent options.
   - Decision Outcome: one line `Chosen option: **...**, because ...`, then checkable statements.
   - Consequences (good and bad) and Confirmation: the test, lint rule, import-linter contract or CI job that checks
     compliance — grep that it exists — or "review only".
   - More Information (optional): related ADRs, research, open questions.
5. Superseding or amending: set `superseded-by: NNNN` and `status: superseded` (or `amended-by: NNNN`) on the old ADR.
   Front matter is the only part of an accepted ADR that may change.
6. Do not edit the index table of `docs/adr/README.md`: it is generated. Run `make docs` (in Heinzelmännchen)
   and commit the result; `uv run pytest tests/unit/documentation_test.py` checks template, index and links.
7. Status stays `proposed` until a maintainer accepts it. Commit with `docs(adr): ...`.
