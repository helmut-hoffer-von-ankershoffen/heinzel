---
status: proposed
date: 2026-10-25
implementation: built
decision-makers: maintainer
amends: 0064
related: 0044, 0060
implemented-by: 175
---

# 0073: Themes supply safe SVG layers for personal avatars

## Context and Problem Statement

Themes currently change avatar colours and add a built-in snow decoration. A theme needs its own original
winter artwork while retaining each person's age, beard, build, colours, state and existing gestures. The SVG
file boundary already checks theme logos and serves them from an owned, same-origin route (ADR 0044).

## Considered Options

- **Optional semantic SVG layers over the original drawing** (chosen).
- **Fixed full figures for every state**: replacement hides personal choices and duplicates gesture/expression
  logic. Defer until a theme can declare supported look features and fixed choices explicitly.
- **Arbitrary inline SVG or theme JavaScript**: unnecessary executable content for an artwork contribution.

## Decision Outcome

Chosen option: **optional semantic SVG layers**, because original theme artwork can dress the personal drawing
without replacing its identity or expression.

- The optional `[heinzel.assets]` table maps closed slots `hat-young`, `hat-grown`, `hat-old` and
  `outfit-baby`, `outfit-young`, `outfit-grown`, `outfit-old` to local SVG files. Missing slots use the core drawing.
  A baby has no hat. Assets are optional independently of palette and built-in decorations.
- Hat layers use the head's local coordinates: a `0 -240 512 840` canvas. They share its build scaling, bow and
  `hz-hat` gesture group. Outfit layers use a `0 0 512 860` canvas, scale with shoulder width, and sit underneath
  explicit accessories. Core eyes, beard, arms, state badges and personal colours remain drawn.
- Layers are ornamental additions; they must not replace the face or conceal personal styling. Originality,
  consistent line/shape language and respectful placement are checked in review and browser previews.
- Every file follows the existing logo path, 200 KB bound and SVG validation: no scripts, entities, event
  handlers or external references. Validation runs on theme declaration and again when resolving bytes/URLs.
  Layers use static geometry and presentation attributes; CSS and SMIL are refused so theme assets cannot bypass
  reduced motion. They load as SVG images, never inline markup. No new CSP exception is introduced.
- Owned `theme-assets` URLs name closed slots and carry the content hash. A changed, missing or unsafe file
  contributes no URL; an image load failure hides only that layer, leaving the complete personal avatar visible.
  Theme listing semantics match CLI, API and MCP.
- App/PWA, native notification and splash replacement are separate extensions. They retain their original
  artwork and opaque-white app icon contract in this slice; the plugin inventory labels those slots proposed.

### Consequences

- Good: private themes can ship actual original snowy artwork while all personal choices and motions survive.
- Bad: every supported age needs intentionally aligned layers; arbitrary full icon sets remain future work.

### Confirmation

Kernel tests reject invalid slots, traversal and unsafe SVGs, and recheck changed files. Theme/web integration
checks versioned URLs, owned route lookup and fallback. Avatar tests exercise age/build/state, gestures and
accessories with asset layers, including failed images. A browser preview verifies the private winter set.
