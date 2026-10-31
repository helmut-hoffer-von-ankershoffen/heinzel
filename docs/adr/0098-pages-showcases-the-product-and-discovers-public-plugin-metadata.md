---
status: proposed
date: 2026-10-30
decision-makers: maintainer
amends: 0082
related: 0094, 0095, 0097
implemented-by: 278
---

# 0098: Pages showcases the product and discovers public plugin metadata

## Context and Problem Statement

The maintainer requires an excellent GitHub Pages site that converts exploration into installation and contribution,
explains the product vision and architecture, and showcases plugin functionality and theme artwork. The current
site projects repository documents and keeps five plugin links in navigation. After ADR 0094, core deliberately
installs no real plugins, so an installed-provider gallery cannot describe the public ecosystem. The website must
preserve that boundary and update when a plugin changes without requiring a core edit.

## Considered Options

- **Extend the existing MkDocs site with a public metadata catalog and independent refresh** (chosen).
- Install every discovered plugin while building core documentation: executes third-party code and couples delivery.
- Maintain a central list of plugin names, screenshots and versions: duplicates ownership and drifts.
- Replace the documentation toolchain with another application framework: adds dependencies without improving the
  source ownership, publication or contribution contract.

## Decision Outcome

Chosen option: **a product-led MkDocs site with a data-only public catalog**, because one site can explain, demonstrate
and document Heinzel while preserving independently delivered plugins.

- The homepage offers installation, plugin exploration and contribution. Dedicated guides explain the first useful
  workflow, available functionality, vision, architecture and contribution. Roadmap and preview capabilities are
  distinguished from released behavior; no fabricated usage counts, testimonials or maturity claims are published.
- The public catalog discovers `heinzel.modules` project metadata in public collection directories and repositories
  carrying the `heinzel-plugin` topic. It reads bounded metadata and declared artwork/theme files at immutable Git
  revisions. It never imports, installs, records or executes discovered implementations.
- Optional `[tool.heinzel.catalog]` metadata lives beside the owning plugin's project definition. It supplies display
  name, category, highlights and relative theme/artwork paths. A minimal plugin remains discoverable without it.
  Adding a plugin or theme requires no core renderer, navigation, registry or site source edit.
- Repository visibility comes from GitHub, not author-supplied metadata. Private repositories and their contents
  never enter public snapshots. Paths, URL schemes, file kinds, sizes, SVG output and untrusted text are checked;
  source text is escaped before HTML rendering. Source provenance and catalog freshness remain visible.
- Each plugin gets a static, indexable detail page and an immutable source-install command. The catalog supports
  accessible filtering and search, useful empty states, keyboard operation and a complete no-JavaScript fallback.
  Themes have generated light/dark palette previews; owner-supplied raster artwork remains attributed to its source.
- The recommended app version advances only when a published standard profile and its matching public core package
  exist with matching wheel digests. A newly tagged but incomplete rollout cannot replace a working install command.
- A checked-in public snapshot makes local site builds reproducible and offline. Publication refreshes an ephemeral
  snapshot on documentation pushes, releases, manual dispatch and an hourly schedule. It uses only read access to
  public metadata, with no cross-repository publishing token. A failed refresh fails publication and preserves the
  already deployed site. Refreshing the website does not commit generated data back to core.
- Existing kernel-derived CLI/configuration reference and plugin-owned runtime documentation remain authoritative.
  Metadata-only catalog claims are labeled as declarations, not compatibility certification. Site presentation and
  tooling are documentation assets, outside the runtime package and its dependency graph.

### Consequences

- Good: users can understand the product, find relevant plugins and install a complete release from one place.
- Good: authors own their showcases; website publication needs no plugin environments or core release.
- Good: an offline build and provenance make the result reviewable without live service dependencies.
- Bad: GitHub topic indexing and hourly publication introduce a visible discovery delay.
- Bad: metadata cannot prove runtime behavior; owning acceptance and release attestations remain necessary.

### Confirmation

Catalog tests use arbitrary synthetic projects to prove discovery without registration or imports, private-source
exclusion, hostile text/path rejection, theme rendering and complete-release selection. Site browser checks exercise
installation links, filtering, keyboard/copy behavior, no-JavaScript content, mobile layout, both color modes and
accessibility. CI builds the site independently of application/plugin builds; the Pages workflow publishes only
validated static output. The authoring guide documents the metadata contract and automated refresh behavior.
