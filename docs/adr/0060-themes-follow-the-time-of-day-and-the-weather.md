---
status: proposed
date: 2026-10-24
decision-makers: maintainer
amends: 0044
amends-0044: variants that follow signals; a base theme is checked for contrast too
related: 0003, 0027, 0039, 0043
implemented-by: 120
---

# 0060: Themes follow the time of day and the weather: variants on a closed vocabulary of signals

## Context and Problem Statement

A theme ([ADR 0044](0044-themes-are-data-a-plugin-ships.md)) is one set of colours per mode. The maintainer asked for
themes that are alive: a community weather plugin offers a "Weather" theme whose colours change with the weather and
through the day, with backgrounds behind the main area, and any plugin's theme should be able to do the same while
staying data and safe. So the questions are what a theme says about when its colours change, where the facts about
now come from, how a background is described without opening a way to load or run anything, and how text stays
readable whatever the theme chose.

## Decision Drivers

- A theme stays data checked at import (ADR 0044): no expression language, no CSS text, no `url()`.
- Heads are views ([ADR 0003](0003-heads-are-views-on-operations.md)): the facts about now come from an operation
  every head can call, `hz system theme signals`, not from browser code.
- Nothing is sent home ([ADR 0027](0027-nothing-is-sent-home.md)): the core reads no weather service; a plugin that
  does says so like any adapter.
- Readable beats pretty: a theme whose text falls under WCAG AA anywhere is refused, not trusted.

## Considered Options

- **`[[variant]]` blocks with a `when` on a closed vocabulary of signals; signals published by
  `@module.signals(every=...)`, a read-only operation returning `ThemeSignals`; backdrops as typed gradient parts; a
  contrast check at import** (chosen)
- **Signals as free-form key/value data any plugin defines**: a theme could name anything, nothing could check a
  misspelt signal, and the vocabulary themes share would fragment per plugin. A closed list grows by an ADR.
- **A field on `ThemeLet`** (a theme that knows its own weather): it ties the facts to one theme, so a second theme
  can't follow the same weather, and a weather plugin that offers no theme couldn't publish at all.
- **Pushing signals over the notice stream (ADR 0043)**: signals change every few minutes at most, and the stream
  is for notices a person reads; polling one cheap operation once a minute is simpler and every head can call it.
- **Gradients as CSS strings checked by a grammar**: the parser would be the attack surface. Typed parts (kind,
  angle, two to four stops, opacity) leave nothing to parse; the web app builds the CSS from them.
- **Combining every matching variant**: the number of combinations to check for contrast grows with every variant;
  one winner per state keeps the check exact.

## Decision Outcome

Chosen option: **variants on a closed vocabulary of signals, published by a read-only operation, with typed
backdrops and a contrast check at import**, because a theme stays data that can't reach outside its declarations,
the facts about now come from an operation like everything else, and readability is checked for every state.

- **Signals** (`heinzel.kernel`, `_theme_signals.py`): `daypart` (`dawn`, `morning`, `day`, `dusk`, `evening`,
  `night`) and `weather` (`clear`, `clouds`, `rain`, `drizzle`, `thunder`, `snow`, `fog`, `wind`, `heat`, `cold`).
  `ThemeSignals` is what a module publishes: `weather`, `sunrise` and `sunset` (aware datetimes), unknown keys
  refused. `@module.signals(every="15m")` declares a read-only operation returning it and a `SignalLet` (1m to 1d).
- **The daypart** is computed by `system.theme` from `[org] timezone` (the user's files or `HZ_ORG__TIMEZONE`, never
  a project file), else the machine's zone, and today's sunrise and sunset when a module knows them, else 06:30 and
  18:30: dawn 45 minutes either side of sunrise, morning until 11:00, day until an hour before sunset, dusk until
  half an hour after it, evening until 22:30, night until dawn; sun times out of that order fall back.
- **`hz system theme signals`** returns the daypart, the weather, the sun times, the zone and the modules whose
  signals are known. Each module's signals operation runs at most once per its interval; one that fails or returns
  anything but valid `ThemeSignals` leaves its signals unknown until the next interval. Of several modules publishing
  one signal, the first by module order wins. The web app asks again every `refresh` seconds (60).
- **Variants**: `Theme.variant`, at most 32 `[[variant]]` blocks, each a `when` (`daypart` and/or `weather`, a word
  or a list of words of the vocabulary; at least one) and `light` and/or `dark` overrides of any colour token,
  `main_tint` and `backdrop`, under the same grammar as the base. The variant whose `when` names the most signals and
  matches wins, the first declared on a tie; a signal nobody publishes matches nothing, so the base applies.
- **Backdrops**: `backdrop` in a mode table (base or variant) is `gradient` (`linear` or `radial`), `angle` (0 to
  360, linear), two to four `stops` (a hex colour or a colour token of the theme) and `opacity` (0 to 0.9, default
  0.6). The web app sets `--theme-backdrop` from these parts only, each stop as `color-mix(in srgb, <hex or
  var(--color-token)> <opacity>%, transparent)`, and checks them again; anything else draws `none`. It is drawn
  behind the main area only: the sidebar, cards and dialogs stay solid.
- **Contrast**: for each mode, the base and the base with each variant applied, `foreground` and `muted_foreground` must reach 4.5:1 on
  `card`, `sidebar`, `popover`, `background`, `main_tint` and each backdrop stop blended over the
  background at its opacity. A theme failing anywhere is refused at import with every failing pair named. This
  amends ADR 0044: a base theme is checked too.
- **Transitions**: the main colour tokens are registered with `@property` as `<color>`; when the variant of the
  theme in use changes, the web app sets `.theme-shift` on `<html>` for two seconds, which fades them over 1.5 s. A
  new theme or a switch of mode applies at once, and `prefers-reduced-motion` turns every transition off.
- **Previews**: each theme's preview shows the variant the signals pick now; in expert mode "Preview conditions"
  picks a daypart and weather for the previews only.

### Consequences

- Good: a weather plugin needs no UI code: it publishes `ThemeSignals` and ships a TOML theme with variants.
- Good: any theme can follow the time of day without any plugin, since the core computes the daypart.
- Bad: a theme that ADR 0044 accepted with hard-to-read colours is now refused at import; its plugin must fix it.
- Bad: the signals operation of a plugin runs on the web head's request when its interval passed; a slow one delays
  that answer, so the docs ask for a cheap one.

### Confirmation

`tests/unit/kernel/themes_test.py` (variant grammar, specificity, backdrop grammar, contrast in both modes and every
variant), `tests/unit/kernel/theme_signals_test.py` (daypart, vocabulary, `module.signals`),
`tests/integration/theme_test.py` (signals through the CLI and the web head, once per interval, failures unknown) and
`web/src/layout/Variants.test.tsx` (variant switching, backdrop CSS from checked parts, the fade, reduced motion,
preview conditions).

## More Information

A second step adds layered wallpapers (sky, clouds, particles, stars bound to signals) as checked assets served from
`/theme-assets`, selectable apart from the theme; it gets its own ADR. The weather theme's how-to is in
[docs/THEMES.md](../THEMES.md).
