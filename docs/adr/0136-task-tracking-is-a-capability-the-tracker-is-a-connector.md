---
status: proposed
date: 2026-11-07
implementation: partial
decision-makers: maintainer (the decision), architect (shape)
amends: 0028
amends-0028: the tracker capability writes
related: 0003, 0019, 0021, 0029, 0065, 0068, 0081, 0094
implemented-by: 572
---

# 0136: Task tracking is a capability; the tracker behind it is a connector

## Context and Problem Statement

People and their Heinzelmännchen need one shared board of work: what was asked, who has it, whether it is done and in
which release. The team that builds hz wants to run its own build loop on hz, with the same board its human coordinator
and third-party coding agents use. The kernel defines a read-only `Tracker` capability today (`issues_updated`); nothing
writes to a tracker, and the app shows no issues. Which part of this is core, which is a plugin, and what is the system
of record?

## Decision Drivers

- The core has no concrete plugin dependency and no vendor client (ADR 0094); everything specific is a plugin.
- One contract, many trackers: a team keeps the tracker it has.
- Vendor-neutral access: a CLI, MCP, REST and the web app expose the same operations (ADR 0003, 0019); other agents and
  tools must reach the same board.
- Plain words for people: no tracker, agent, branch or database vocabulary outside an expert mode.
- Authority by code (ADR 0068): writes that publish go through approval proofs; every write names an actor.
- Private by default: issue text stays in the owner's tracker and never reaches a public repository.

## Considered Options

- **A capability contract in the kernel, tracker adapters in plugins, a plain-words issues plugin on top** (chosen).
- **A tracker built into the core** (git-backed markdown cards): a second system next to the tracker a team already has;
  the storage idea survives as one adapter.
- **A third-party tracker only, no Heinzel contract**: the owner, the Heinzelmännchen and external agents would not
  share one board, and the app could show nothing.

## Decision Outcome

Chosen option: **capability contract, connector adapters, issues plugin**, because it keeps the kernel small, lets any
tracker be the system of record, and gives every head and every agent the same board.

- The kernel's `Tracker` capability gains write methods `create`, `claim`, `transition`, `note`, `attach` and `close`
  (mode `write`); `Issue` gains `created_at`, `closed_at`, `reporter`, `released_in`, `released_at` and `attachments`.
  Read-only operations never receive a write handle (ADR 0028).
- An adapter is a plugin. The first is a Beads adapter that talks HTTP to the tracker's own loopback API with a bearer
  token; it imports no tracker code and reads no database file. Later adapters: git-backed cards, GitHub issues, Linear,
  Jira.
- An issues plugin provides the plain-words operations (`report`, `list`, `show`), three states (Received, Being worked
  on, Done, and "Released in <version> on <date>"), a state-change notification through the notifier, and a
  capture-and-annotate report flow in the web head (DOM rasterized through an SVG `foreignObject`, redaction before
  pixels exist, size caps, originals kept private next to the issue). Expert mode is the only place identifiers appear.
- Every write carries an actor (the person, the Heinzelmännchen or the external client); claims are atomic and guarded;
  a stale claim is reclaimed, never overwritten.
- The release step stamps each issue with the first release tag that contains its merge commit (`git tag --contains`),
  which is what "Released in" shows and what measures ask-to-release.
- The board is reachable by other agents through the tracker's own CLI and API on the machine, and over the user's
  private network with per-client tokens; never on the public internet.

### Consequences

- Good: one board for people, Heinzelmännchen and third-party agents; the core stays vendor-free; the app can show work
  without a tracker of its own; dogfooding the build loop starts on real data.
- Bad: a second core version must ship before the plugin can; the Beads adapter depends on that tracker's HTTP API
  staying compatible; attachments need per-tracker handling until every adapter supports them; the MCP HTTP head needs
  bearer tokens and scopes before remote clients can use it.

### Confirmation

- Import-linter contract: no module under the core imports an adapter or tracker client.
- Capability snapshot test: `tracker` lists the new methods with their modes; the issues plugin's operations declare
  `read_only` correctly.
- Unit test: the plain-state mapping covers every tracker status in the fixtures.
- Integration test with an in-memory fixture tracker (`tests/integration/tracker_test.py`): report, claim, note, attach,
  transition and close round-trip with every actor recorded; a read-only operation cannot reach a write in any head. The
  release stamp and read tokens come with the adapter and the issues plugin.
- Review: every write operation names its actor and, where it publishes, carries an approval proof.

## More Information

Revisit when a second tracker adapter exists and when the coordinator role has run on hz for three days.
