---
status: proposed
date: 2026-10-25
decision-makers: maintainer, architect and security reviewer
amends: 0056
related: 0026, 0039, 0065, 0068
---

# 0072: Plugins declare navigation and Settings metadata as checked data

## Context and Problem Statement

Status items, actions and widgets already come from module declarations, but Settings and sidebar navigation still
contain plugin-name choices in the web app. That can leave a Clock page visible when its provider is absent and
makes an arbitrary installed plugin's Settings depend on a frontend change. A semantic icon belongs to the plugin
that knows what it does. This change extends the existing data contribution system; it does not load plugin React
code or add a renderer for an arbitrary route.

## Decision Drivers

- An installed, enabled plugin supplies its own labels and checked semantic icons.
- Missing or disabled providers leave no stale navigation target.
- Settings ownership and operation effects follow existing kernel/module boundaries.
- CLI, MCP and authenticated web/API can inspect the same projection, with no private arguments in URL queries.

## Considered Options

- **Static module navigation declarations projected by `system.ui`** (chosen): the existing contribution consumer
  already resolves providers and validates effects.
- Keep a frontend map from plugin names to icons/pages: rejected because every new plugin needs a core edit and
  absent providers can leave ghost links.
- Implement a rich plugin code loader first: deferred to ADR 0065; checked metadata can fix this independently.

## Decision Outcome

Chosen option: **`module.nav(...)` declares checked navigation and Settings data**, because the plugin owns its
identity while the core retains routing, rendering and authorization.

- Add public frozen `NavLet` and checked `NavSettings` shapes, alongside existing contribution lets. A declaration
  has `name`, `title`, `icon`, `order`, optional `settings {sections, group}` and optional `sidebar` effect; at least
  one target is required. Its id is `<qualified-module>:<name>`; legacy Settings metadata reserves `:@settings`, which an explicit name cannot use. Reuse `Icon`, effect validation, bounded display
  text and order. `cloud-sun` is an additive semantic icon, not a special case for a named plugin.
- Settings groups are the existing `preferences`, `customize`, `organisation` and `computer` headings. Sections are
  qualified module prefixes owned by the declaring module or its descendants, matched against active SettingsLets.
  A plugin cannot claim another provider's settings, the kernel settings or an invented section. Discovery-time
  shape checks and consumer-time ownership checks remain distinct.
- `system.ui navigation` returns `{id, module, declared, plugin, title, icon, order, settings?, sidebar?, problem?}` from active kernel
  records. It does not run a source operation merely to list navigation. Invalid declarations are diagnosed and
  do not produce an executable target. Disabled/uninstalled providers are omitted. Valid explicit Settings claims
  replace SettingsLet-derived compatibility entries; undeclared legacy Settings still have a generic fallback.
  `declared` distinguishes explicit NavLets from compatibility entries, so consumers preserve host-owned Settings
  groups without guessing from titles, icons or plugin names.
- Sidebar effects use the existing `Navigate`, `OpenUrl`, `RunOperation` and `RunCommand` grammar and consumer.
  Operation effects remain confined to the contributing module and receive the server-built canonical call. A
  declaration cannot register arbitrary JavaScript, external frame origins or a new page implementation.
- Consumers use entry ids/section metadata rather than core maps from plugin names to titles or icons. Arbitrary
  plugin fixtures prove the behavior; first-party Clock uses the same public declaration seam. Contribution refresh
  remains for dynamic values; static navigation does not invent a polling callback or execute plugin code per row.
- Add optional `OperationCall.body` for private mutation effects (ADR 0068). Such a call has an empty `query` and
  the validated flat JSON argument object in `body` (values may be nested declared models/lists); the generic UI sends it as JSON with no sensitive-URL fallback.
  Fixed arguments are finite JSON, bounded to 32 parameters, 8 nesting levels and 16 KiB per parameter.
  Existing scalar calls retain their query representation; structured arguments require the private JSON body. This metadata cannot issue human approval proof, and rendered
  labels do not replace service authorization or the authoritative approval preview.

### Consequences

- Plugins add discoverable Settings/sidebar metadata without a frontend plugin-name switch or a rich-code loader.
- The shared public API and projection gain additive shapes; plugins adopting them must declare the compatible
  core lower bound. Existing Settings declarations continue to work through an explicitly generic fallback.
- Static metadata cannot make an unimplemented route render; a plugin uses existing effects and supported views.
  Rich custom views remain a separate SDK/runtime decision under ADR 0065.

### Confirmation

New unit and integration tests will cover declaration bounds/icons/targets, foreign or absent Settings ownership,
arbitrary providers, explicit-over-legacy precedence and disabled/uninstalled modules. Web tests will cover generic
Settings/sidebar consumption and missing Clock provider. A private contribution-action regression will verify the
same JSON body as direct invocation and absence of sensitive URL arguments. Existing contribution and public-API
snapshot tests, generated OpenAPI/client checks and import contracts remain required.
