---
title: Helpers for good work
description: Heinzel's vision, current foundation, and the principles behind an open-source AI operating system for product teams.
---

<p class="hz-eyebrow">The direction is human</p>

# Busywork done. You decide.

Every team has work nobody chose: the tracker row, the Friday report, the customer note that should have been
filed yesterday. Heinzelmännchen are helpers that do the drafting. People approve.

The name comes from the helpers of Cologne, who finished the town's work at night. Ours are less magical: a
helper reads your tools with your own logins, writes a draft where the work lives, and asks you.

## The goal

A crew of durable agents that take over any busywork that can be automated. Documents, sheets and tickets are
one kind; a calendar, a mailbox or a review queue are others. Each helper keeps one promise and has a name, a
face, one continuous conversation and a record. Eventually the helpers of a team coordinate through shared
artifacts, so one person's approved output becomes another's input. Nobody designs that workflow; it emerges
from the promises people made.

Humans stay in control. Nothing is sent, published or spent without approval. Approving a kind of action
automatically is opt-in, scoped, revocable and earned on a record.

## What exists today

The foundation is released: a kernel and typed operations; CLI, MCP, REST and web heads; local drafts on a
schedule; named helpers with their own chat; team repositories; and plugins with their own releases.

[Install the foundation](guides/install.md) · [Explore the catalog](CATALOG.md)

## What does not exist yet

- No autonomous job has run end to end with a live provider.
- No helper writes into a shared artifact, and no two helpers have worked together.
- Learning from your edits is a plan.

Jobs shown on this site as helpers are imagined unless a page says otherwise. A plugin in the catalog does not
mean a finished workflow.

## Principles

| Principle | In the software |
| --- | --- |
| You stay in control | Drafts first; approval and waiting states are visible. |
| Your identity | A helper acts with your logins, never as a bot account. |
| Local by default | Your machine first; remote access is an explicit choice. |
| Any model or agent | Typed operations and MCP work with different tools. |
| Open and composable | Public contracts, independent plugins, MIT license. |

Longer reasoning: [INTENT.md](../INTENT.md), the [architecture tour](guides/architecture.md) and the
[ADRs](adr/README.md).

## Help shape it

Build a plugin, design a theme, fix a confusing step, or report a problem.

[Find your contribution path →](guides/contribute.md)
