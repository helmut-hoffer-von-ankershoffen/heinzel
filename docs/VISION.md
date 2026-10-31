---
title: Helpers for good work
description: The vision for Heinzelmännchen, the current foundation, and the principles behind helpers that draft a product team's busywork for approval.
---

<p class="hz-eyebrow">The direction is human</p>

# More time for the work that matters.

Product teams spend too much attention keeping tools and people in sync. Heinzelmännchen aims to make the
repeatable parts of that work something small helpers can draft, organize and prepare, with people deciding
what goes out into the world.

The name comes from the helpers of Cologne: little people who finished the town's work at night. Our ambition
is practical rather than magical: make the work explicit, connect the right tools, show what happened, and ask
for a person where a person's judgment belongs.

## The foundation is available

Today's Heinzelmännchen release supplies a kernel and typed operations; CLI, MCP, REST and web heads; configuration and health
checks; workflow and team foundations; and plugins with independent build, test and release cycles. The standard
app includes everyday utilities and a theme. Optional connectors and source examples broaden the surface.

[Install the foundation](guides/install.md) · [Explore what is available](CATALOG.md)

## The direction we are building toward

An open-source crew of AI helpers for product teams: helpers that maintain context, prepare updates, draft
agendas and reports, and make repeatable team workflows easier to inspect and approve. Rich end-to-end team
automation is still evolving. A connector's presence in the catalog does not mean every envisioned workflow
is production ready.

## Principles that shape the design

| Principle | What it means in the software |
| --- | --- |
| People stay in control | Approval and visible states are part of the work, not an afterthought. |
| Local by default | A local workspace is the starting point; remote access is an explicit choice. |
| Model and agent agnostic | Typed operations and MCP let different tools use the same capabilities. |
| Open and composable | Public contracts, independent plugins, ordinary Python dependencies and an MIT license. |
| Useful to the whole team | Team context and workflow design matter alongside code. |
| Small enough to improve quickly | Test core without providers; test a plugin without all of core. |

The authoritative intent and longer reasoning live in [INTENT.md](../INTENT.md). The
[architecture tour](guides/architecture.md) shows how these principles become boundaries, and the
[ADRs](adr/README.md) record the decisions we can revisit with evidence.

## Help shape it

Build a useful plugin, design a theme, improve a workflow, document a confusing step, or report a reproducible
problem. Start with a change that has a clear outcome and a short feedback loop.

[Find your contribution path →](guides/contribute.md)
