---
status: proposed
date: 2026-11-04
implementation: built
decision-makers: maintainer
amends: 0098
related: 0047, 0094
implemented-by: 319
---

# 0120: Plugin previews read public inert metadata

## Context and Problem Statement

People need to inspect a plugin's UI and CLI before installing code. The public website already publishes
owner-declared screenshots and soundscapes; its metadata can also serve the app without a provider dependency.

## Considered Options

- **Reuse bounded public declarations with source validation** (chosen).
- Install plugins to capture previews: executes code before the person can inspect it.
- Keep a separate core screenshot registry: duplicates plugin ownership and requires core changes for every plugin.

## Decision Outcome

The authenticated plugin showcase operation reads bounded inert metadata. It never imports, installs or runs the
selected plugin. CLI and stdio MCP share the operation; unauthenticated API and network MCP omit it.

Read the published catalog first, then pinned anonymous raw files, then GitHub metadata when a revision or file
tree is needed. Catalog media must belong to the exact public repository, project directory and immutable revision.
An installed source takes precedence over a same-name discovery result. Unknown and private installed sources
show a notice rather than resolving a different public project. Project configuration cannot redirect the feed.

Catalog requests allow HTTPS on heinzel.team and raw.githubusercontent.com only. This permits custom public
catalogs on GitHub without exposing arbitrary destinations, local addresses or credentials to server requests.

Requests reject credentials, alternate ports, queries, fragments, traversal and redirects. Raw metadata requests
also require an immutable revision. GitHub API credentials stay on api.github.com; conditional caches are isolated
by credential identity. Fixed metadata, tree, image and soundscape limits remain enforced.

The app reads metadata and external images only after opening Screenshots. Captions remain text; images omit
referrers. UI and CLI captures have separate accessible controls, with a full-size link. Soundscapes are explicit
external YouTube links, with no automatic player or request. Empty previews do not prevent installation.

### Consequences

The catalog's source declaration and screenshots describe the owner's captures, not compatibility certification.
Catalog-backed previews rely on the publisher's bounded public-source validation. A stale catalog names its actual
revision; an installed different revision is read separately. Missing soundscape discovery is reported explicitly.
Offline fixtures prove browser and CLI behavior; actual public catalog availability, physical devices and released
application acceptance remain separate checks.

### Confirmation

Synthetic plugin tests cover path and URL attacks, private sources, size limits, immutable source precedence,
credential cache isolation and authenticated head parity. Built browser fixtures exercise lazy loading, UI/CLI
selection, explicit soundscape links, keyboard controls, touch targets, overflow and accessibility at desktop and
six phone viewports in Chromium and WebKit. No concrete plugin implementation is installed for these checks.
