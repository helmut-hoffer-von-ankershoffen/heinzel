---
status: accepted
date: 2026-10-29
decision-makers: maintainer
amends: 0058, 0094
related: 0044, 0060, 0092
---

# 0095: The standard Heinzelmännchen theme owns the bundled soundscapes

## Context and Problem Statement

Theme plugins already declare themes and soundscapes through public lets, without core registration. Core still
ships three soundscape definitions beside the playback service. The maintainer requires a Heinzelmännchen theme
that is automatically installed and brings that content, while core remains independently buildable and testable.

## Considered Options

- **A standard-profile theme plugin owns the content** (chosen).
- Keep soundscape definitions in core: content changes still require a core release.
- Move the playback and theme engines too: exceeds the approved 0.9.0 module extraction scope.

## Decision Outcome

Chosen option: **a standard-profile Heinzelmännchen theme plugin owns its theme and the former core soundscapes**,
because existing public contracts provide the complete content boundary.

- `heinzel-plugins/plugins/heinzel-theme` owns the `heinzelmaennchen` theme, its logo assets, and Claude FM,
  Die Heinzelmännchen (`focus`) and Hyper Focus soundscape definitions. Their names and stream identities remain
  unchanged. `focus` pairs with the plugin's theme through `pairs_with`.
- The plugin declares standard-profile membership in its own metadata. The generic composer includes and enables
  it alongside the other standard plugins without a concrete package list in core. Explicit user disable/removal
  choices survive profile refreshes. Installation does not select a different theme or start sound playback.
- The three-default composition observation in ADR 0094 records the phase before this amendment. The current
  standard composition has four defaults: Clock, Resource, Tailscale and this theme. Earlier timings remain
  historical measurements, not measurements of the expanded profile.
- Core keeps generic theme/sound contracts, playback, selection and a zero-plugin fallback appearance. It contains
  no bundled soundscape definitions and does not import, depend on, or recognize the theme plugin's identity.
- Core tests use synthetic soundscape contributions. The plugin tests its actual catalog, pairing, packaged assets,
  installed operations and removal fallback against a built core artifact. No real theme plugin enters core tests.
- The theme plugin designs, builds, tests and releases independently. Further theme or soundscape content changes
  are confined to its directory; its dependencies and lockfiles are its own.

### Consequences

- Good: standard users retain the three familiar choices, with explicit plugin ownership and independent delivery.
- Good: minimal core has a working appearance and silent sound picker without optional content installed.
- Bad: the standard app gains one more independently versioned artifact and publishing grant to maintain.

### Confirmation

`tests/integration/sound_test.py` proves minimal core offers only Off and exercises arbitrary test contributions.
The theme plugin's unit and installed-wheel acceptance tests check its theme, all three stream identities, pairing,
logo serving and fallback after disabling/removing the provider. The generic standard-profile composition check
installs all projects enrolled by metadata and verifies the exact enabled set.
