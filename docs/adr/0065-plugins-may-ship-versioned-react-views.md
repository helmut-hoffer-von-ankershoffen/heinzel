---
amended-by: 0094
status: proposed
date: 2026-10-25
decision-makers: maintainer (product), architect (technical shape)
amends: 0056
related: 0026, 0039, 0044, 0045, 0047, 0058, 0060
---

# 0065: Plugins may ship versioned React views alongside their Python declarations

## Context and Problem Statement

[ADR 0056](0056-modules-contribute-status-items-actions-and-widgets-as-data.md) restricts plugin UI to data rendered
by the host. The maintainer has chosen a Python part and a React part for plugins, while retaining data shapes
where they suffice. Interactive pages, right-sidebar panels and chat views need a common packaging and action
contract. The [extension guide](../PLUGINS.md) distinguishes shipped surfaces from proposed ones.

## Decision Drivers

- Share one React runtime and preserve accessible controls and the host's approval gates.
- Retain useful CLI and MCP output when an interactive view is unavailable.
- Isolate failures without claiming same-realm JavaScript is sandboxed.

## Considered Options

- **Trusted, versioned ES modules in the Python wheel, with a host SDK and data-first fallbacks** (chosen).
- **Only closed data shapes:** retained for common contributions, insufficient for specialist interactive views.
- **Remote bundles:** rejected; installed code could change without a plugin update or review.
- **Sandbox every view in an iframe:** useful for untrusted generated content, but cannot share the host React
  runtime or ordinary component composition; it requires a distinct protocol.

## Decision Outcome

Chosen option: **allow trusted plugins to package versioned React views beside Python declarations**, because rich
interfaces should reuse the host's data, controls and operation gates.

1. The wheel contains a prebuilt ES module, local assets and a checked manifest naming its distribution, entry point,
   supported SDK range, slots and text fallback. Incompatible or invalid bundles do not load; the fallback and a
   runnable fix remain. No remote code download or plugin-selected CSP exception is allowed.
2. A proposed `@heinzel/plugin-sdk` supplies props, query hooks, navigation, theme and host actions. The host supplies
   React, react-dom and the UI kit once. The template's Vite library build excludes those runtimes.
3. Rich slots start with pages, right-sidebar panels, chat results and specialist Settings panes. Every view is
   explicitly registered and wrapped in an error boundary. Footer items, header actions, common widgets and Settings
   fields retain their data contracts. Stable ids, routes and responsive priorities belong to the host contract.
4. Components receive validated data and host actions instead of raw tokens. This is an interface discipline, not
   a sandbox: same-realm code can affect the host realm. Installation already trusts Python with the user's rights;
   confirmation must also describe browser code. Untrusted generated HTML never becomes a trusted plugin module.
5. Capability checks stay in Python. The SDK submits proposals and the host asks the person to confirm; malicious
   trusted code remains inside the installation trust boundary and cannot be treated as a separate security principal.
   Tool results select only installed compatible renderers, with validated data and a text fallback for CLI/MCP.
   Slash commands name operations or skills as data.
6. Themes may eventually ship checked avatar states, face icons, app/notification icons and splash art. Unsupported
   personal choices, states and accessories fall back to core; fixed sets declare their limitations. Apply theme
   path, size and SVG validation. Generate required icon sizes and a reduced-motion splash alternative. Seasonal
   palette/decorations data may ship before full custom sets.
7. The template needs a `web/` library build, SDK types, offline component tests, accessibility checks and a wheel
   content check. CI checks compatibility, shared-runtime exclusion and assets. This ADR does not authorize npm
   publication; initial SDK distribution must be resolved before rollout.

### Consequences

- Specialist interfaces live with the plugin that owns their data, without bespoke core code per plugin.
- Bundle validation and versioning become maintenance responsibilities. Error boundaries protect availability,
  not security against malicious code.
- This design changes the proposed extension direction; it does not add a working React plugin loader.

### Confirmation

Review only for this design. Before shipping the loader, add tests for incompatible manifests, missing assets,
duplicate React runtimes, isolated component failure, keyboard access, reduced motion, absent renderers and gated
writes. A weather view in a page and in chat is the intended first integration test, with CLI/MCP text fallbacks.
