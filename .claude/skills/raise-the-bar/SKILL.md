---
name: raise-the-bar
description: Deep, unhurried review of a whole area of Heinzelmännchen (a module, a head, the release process, the docs, the developer experience) against the best projects of its kind, at the highest effort, to find what would make it excellent rather than acceptable. Use when asked to raise the bar, before a release, or before a design decision with long-lasting consequences; not on every change.
---

# Raise the bar

Expensive on purpose: the most capable model at maximum effort. Run it when the decision outlives the change.

1. Name the area and the question in one sentence ("Is the plugin API as small as it can be?"). Collect the files,
   ADRs and docs that define it.
2. Start the `architect` agent with `effort: max` and that brief. Ask it to compare the area with two or three
   well-regarded projects that solve the same problem (cite them), and to return: what is already excellent, the
   three changes with the most leverage (each with cost and risk), and what to stop doing.
3. If the area touches security, CLI/DX or docs, start the matching reviewer in parallel with the same brief and
   `effort: high`.
4. Turn the result into decisions: an ADR for anything structural (skill `write-adr`), issues or pull requests for
   the rest. Report the decisions, not the transcript.
