---
status: proposed
date: 2026-10-24
decision-makers: maintainer
related: 0039, 0052
---

# 0053: The team graph is drawn with React Flow and laid out by elk; Mermaid is only an export

## Context and Problem Statement

[ADR 0052](0052-the-graph-emerges-from-recorded-reads-and-writes.md) decides what the graph is (Heinzelmännchen and
artifacts, "read from" and "wrote to" edges, declared and observed, built by `hz graph` from the action logs) and that
the web app's graph page reads `hz graph`'s JSON. This record decides only how the web head
([ADR 0039](0039-a-web-head-a-react-app-served-by-the-api-head.md)) draws it. The Workflow screen draws its graph with
React Flow and dagre; the team graph has group nodes (a shared tracker containing a row per person) with edges into
the rows, which dagre lays out poorly: it has no compound nodes with edges that cross their border. The page must make
"is each promise kept" visible and never become a scoreboard of people (ADR 0052, "shown as flow, never as
performance").

## Decision Drivers

- Groups with edges into their members, laid out left to right without hand-placed coordinates.
- No new runtime surface: the CSP allows scripts and workers from this origin only (`worker-src 'self'`).
- One model for the page, its export and its tests.
- Licenses on `.license-allowlist`, since the app ships in the wheel.

## Considered Options

- **React Flow, laid out by elkjs's layered algorithm; Mermaid as an export** (chosen)
- **React Flow with dagre, plus a pass that places each group's members inside the group's bounds**: no new
  dependency and MIT throughout; edges into a group's members are routed as if the group weren't there, and the group's
  size is guessed rather than laid out. The fallback if elkjs's license is declined.
- **Render Mermaid in the page** (`hz graph --mermaid`): Mermaid's renderer is large, draws once (no zoom, minimap or
  links per node) and styles through injected CSS; good for documents, poor for a page you click through.

## Decision Outcome

Chosen option: **React Flow, laid out by elkjs; Mermaid only as an export**, because elk's layered algorithm lays out
compound graphs with edges across hierarchy (`elk.hierarchyHandling: INCLUDE_CHILDREN`) and React Flow already draws
the app's graphs in its tokens.

- The page is `/graph` (navigation: Contracts, "Team graph", operation `graph:graph`); the Workflow screen stays.
- `web/src/pages/graph/jgf.ts` is a thin converter from `hz graph`'s JSON (JSON Graph Format) to the page's model; it
  checks shapes, points edges the way data flows, and finds group nodes (`parent`). What it can't read it leaves out;
  only `http(s)` URLs are kept.
- `web/src/pages/graph/layout.ts` is the one layout interface (`layoutTeam(graph)` → React Flow nodes and edges);
  elkjs's bundled build (`elkjs/lib/elk.bundled.js`) runs behind it on the page's own thread, imported lazily as a
  chunk of its own: no worker file, nothing for the CSP to allow.
- Promise states on edges and cards, each with its word as well, and a legend: `on_time` (ok colour),
  `waiting_for_approval` (warn), `late` (bad), `fell_back` (warn, dashed), `offline` (muted, dotted).
- The header line summarises the flow ("Weekly report waiting on 1 update") and never names or ranks a person; a
  summary the server sends wins.
- An artifact node links to its URL in a new tab; a Heinzelmännchen node links to `/heinzel/<id>` and to the chat
  about it (`?chat-context=heinzel:<id>`).
- "Copy as Mermaid" writes the same model as a Mermaid flowchart. Without the `graph` module, or on a 404, the page
  says to run `hz graph`.

### Consequences

- Good: groups and edges into them lay out without special cases; the export and the page can't disagree.
- Bad: one more runtime dependency, about 1.4 MB minified (the layout engine is compiled from Java), loaded only by
  this page.
- Bad: elkjs is `EPL-2.0 OR GPL-3.0-or-later`. We take it under EPL-2.0, weak, file-level copyleft like MPL-2.0, which
  `.license-allowlist` now lists; we ship elkjs unmodified, and the build
  writes `licenses.md` next to the app (Vite's `build.license`, served at `/licenses.md`) with every bundled
  dependency's license and notice. The engine sits behind one function (`placeTeam` in `place-elk.ts`), so another
  one, such as the dagre option, would replace that file and nothing else.

### Confirmation

- `web/src/pages/graph/jgf.test.ts`: the converter, the direction of reads, groups, the summary, the Mermaid export,
  and that the layout places rows inside their tracker.
- `web/src/pages/graph/GraphView.test.tsx`: states and legend, the group node, links, the export and the empty state,
  each with axe; `web/e2e/ui.mjs` audits `/graph` in Chromium.
- `make licenses` (in `make audit`): elkjs's license is on `.license-allowlist`.

## More Information

- [ADR 0052](0052-the-graph-emerges-from-recorded-reads-and-writes.md) owns the graph's content, the endpoint and its
  JSON; this page follows them.
- Revisit dagre for the Workflow screen if it gains groups too: one layout engine is simpler than two.
