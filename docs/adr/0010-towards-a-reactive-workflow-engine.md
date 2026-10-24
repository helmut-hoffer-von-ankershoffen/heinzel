---
status: proposed
date: 2026-10-23
amended-by: 0020, 0021, 0038, 0049
---

# 0010: Towards a reactive workflow engine

## Context and Problem Statement

Heinzelmännchen's purpose is proactive: do the work first, then ask for approval.
[OpenProse Reactor](https://github.com/openprose/reactor) shows a design that fits our tenets: Markdown contracts
whose `Requires` and `Maintains` sections wire a dependency graph; inputs canonicalized into fingerprints; a memo key
of contract and input fingerprints that decides — deterministically, with no LLM judge and no clock — whether a node
renders or is skipped; one bounded agent session per render; an append-only, hash-chained receipt per run.

## Considered Options

- **Borrow Reactor's ideas and grow the engine out of the kernel** (chosen)
- **Depend on Reactor itself**: it is alpha, so we borrow the ideas rather than depend on it.

## Decision Outcome

Chosen option (proposed): **grow the engine out of the kernel, as new kinds of lets and a new head, without changing
existing modules**, because Reactor's concepts fit our tenets while Reactor itself is alpha.

| Concept | In Heinzelmännchen |
| --- | --- |
| Contract (`*.prose.md`: Goal, Requires, Maintains, Continuity) | `ContractLet`: a workflow Markdown file a module ships; teams add files, not code |
| Change sources (trackers, code hosts, chat, docs) | `WatchLet`: polls a source and returns fingerprinted facets |
| Clocks | `ScheduleLet` |
| Render | an agent session through any agent CLI (Claude Code, Codex, Cursor), provider-agnostic |
| Receipts, approvals, leases, trust ladder | shared state in a Git repository, owned by a `state` module |
| The always-on runner | a `daemon` head (launchd / systemd), one per person, with a lease and a hot standby |

Approvals stay human: every rendered draft is queued for approval unless its owner enabled auto-approval for that
action type.

### Consequences

- The MCP head comes first: it makes Heinzelmännchen's operations available to the agent sessions that renders will
  use.
- Contract format: start with a Heinzelmännchen dialect that is a subset of OpenProse contract Markdown, so we can
  adopt OpenProse tooling later.

### Confirmation

Not yet: proposed and not implemented. Checks are defined with the implementation.

## More Information

The "always-on runner" row is amended by [0020](0020-daemon-head.md); the contract format by
[0021](0021-workflow-contracts.md) (proposed).

Open questions:

- Locks and concurrent writes in Git-backed state (append-only per-person files, short-lived lock branches).
- Which fingerprint canonicalization to standardize on for third-party sources.
