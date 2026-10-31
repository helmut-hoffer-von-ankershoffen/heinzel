---
amended-by: 0094
status: proposed
date: 2026-10-25
decision-makers: maintainer
related: 0039, 0056
implemented-by: 162
---

# 0063: World Clock is a local interactive globe in the right panel

## Context and Problem Statement

The header's World Clock action currently opens a terminal. The maintainer wants a globe beside the work, with the
team's configured time zones, the primary zone highlighted and daylight visible. The clock plugin already returns
the ordered zones; no geolocation or external map service is needed.

## Considered Options

- **COBE, with an accessible text list and static map** (chosen): a small MIT-licensed WebGL renderer with no runtime
  dependencies ([source and API](https://github.com/shuding/cobe)).
- **A full map framework**: unnecessary size and network-facing features for a local time-zone display.
- **Only a text list**: accessible but misses the requested geographic overview.

## Decision Outcome

Chosen option: **COBE, with a text list and static map**, because the globe can stay local and small.

- Pin `cobe` in the web lockfile, bundle it with the app and include its MIT attribution. No CDN or CSP exception.
- The existing clock action navigates to `?panel=world-clock`; this opens the right panel. Closing it restores the
  chat without losing the conversation. It uses the same clock result as the footer.
- Markers use IANA's public-domain `zone.tab` representative coordinates, bundled as data. They are places that name
  time zones, never a person's location. Unknown aliases remain in the text list and are explicitly unmapped.
- First zone is primary. Every zone has a 24-hour time in the text list; selecting it centres the globe.
- Daylight is an approximate solar position computed locally from the UTC date. Label it as approximate.
- Rotate with pointer drag or labelled keyboard buttons. Reduced motion and unavailable WebGL show a static 2D map.
  No automatic spin; the view stops rendering when hidden and cleans up the graphics context when unmounted.
- The decorative map is hidden from assistive technology; the complete time list and controls remain available.

### Consequences

- Good: a geographic view of the team without disclosing configured zones to another service.
- Good: the list works on every device, including when WebGL is unavailable.
- Bad: representative coordinates are approximate; daylight is a visual aid, not astronomical scheduling data.

### Confirmation

In heinzel-plugins, `plugins/heinzel-clock/web/src/ClockPanel.test.tsx` checks primary ordering, unknown-zone fallback, 24-hour times, panel opening and
closing, reduced-motion fallback and graphics cleanup. `plugins/heinzel-clock/web/src/globe.test.ts` checks solar and coordinate
calculations with fixed dates. The web licence check validates the bundled dependency.
