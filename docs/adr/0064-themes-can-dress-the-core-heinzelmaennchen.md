---
status: proposed
date: 2026-10-25
decision-makers: maintainer
amends: 0044
amended-by: 0073
related: 0060
---

# 0064: Themes can dress the core Heinzelmännchen with a palette and snow

## Context and Problem Statement

The maintainer wants a wintry Heinzelmännchen supplied by a theme, with snow on its cap and a frosty scarf. The
original drawing already composes age, beard, hat, shirt, build and accessories from typed shapes. Arbitrary SVG
icon sets are a larger extension; this first slice needs only checked colours and a closed decoration vocabulary.

## Considered Options

- **A checked `[heinzel]` theme table, applied to the existing drawing** (chosen).
- **Arbitrary icon files**: defer to the broader theme-icon-set design; file validation and fallbacks need their own
  implementation, and a fixed icon would lose the person's five look choices.

## Decision Outcome

Chosen option: **a checked `[heinzel]` theme table**, because themes can decorate the drawing while age, beard,
build, state and accessories keep working.

- `Theme.heinzel` is optional. Its `palette` permits only `hat`, `shirt` and `scarf`, each a checked hex colour, and
  `decorations` permits only `snow`, at most once. Unknown keys, CSS and markup are refused.
- Palette colours replace defaults only. A person's explicit hat or shirt colour still wins.
- Snow adds a patch on the cap (none for a baby), static snow dots and a scarf; a scarf already requested is never
  duplicated. It applies to every age, build and state in full, bust and head views.
- Theme changes update mounted avatars; choosing a theme without the table returns to the core drawing.
- No new code, network request, asset route or CSP exception comes from a theme. The plain TypeScript renderer
  remains usable by the icon generator with no theme, producing the same default icons.
- The CLI and MCP theme listing expose the same palette and decorations through the theme model.

### Consequences

- Good: a theme can make a snowy crew without replacing personal look choices.
- Bad: the palette covers three parts, and snow is the only decoration. More parts or full icon sets require an
  explicit extension rather than free-form graphics.

### Confirmation

Kernel theme tests refuse unknown keys, invalid colours and decorations. Avatar tests cover every age and build,
explicit colour precedence, scarf deduplication and returning to the undecorated drawing. Theme-service integration
checks the table reaches CLI JSON. The generated OpenAPI schema describes the same model.
