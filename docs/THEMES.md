# Themes

A theme is data a plugin ships ([ADR 0044](adr/0044-themes-are-data-a-plugin-ships.md)): a TOML file with the
colours of the web app's tokens for light and dark, fonts, sizes and an optional logo, offered with
`module.theme(Path(__file__).parent / "themes")`. `hz system theme list` shows the installed ones, `hz system theme
use <name>` picks one. Every value passes a grammar at import, so a theme can't load or run anything.

Soundscapes work the same way ([ADR 0058](adr/0058-soundscapes-are-data-a-plugin-ships-chosen-like-a-theme.md)): a plugin
offers its own with `module.soundscape(Path(__file__).parent / "soundscapes")`, and they appear in `hz system sound
list` and the web app's picker. The standard app automatically installs and enables the Heinzelmännchen theme
plugin, which owns Claude FM, Die Heinzelmännchen and Hyper Focus. Minimal core contains no soundscape content;
it keeps a fallback appearance and Off. Installation does not change the selected theme or start playback
([ADR 0095](adr/0095-standard-heinzel-theme-owns-bundled-soundscapes.md)).

A theme plugin can declare both themes and soundscapes in its own module, ship their TOML and audio/font/logo
assets, and set a soundscape's `pairs_with` to its theme names. No core edit or central theme/soundscape registry
entry is required. Core discovers public `ThemeLet` and `SoundscapeLet` contributions; Sound follows the selected
theme through the optional public `ThemeChoice` contract, without importing a concrete theme plugin.

The sound options include volume, softer sound while typing and muting during meetings. A stream's mini-player
starts in the sidebar; pop it out, drag it near the sidebar to dock it again, or move the floating player with its
arrow-key button. Docking, undocking and collapsing the sidebar keep the stream playing.

Choosing a soundscape manually turns off **Follow my theme**, so your choice takes effect. Press **Play** to start it.
Turn **Follow my theme** on again to let your theme choose. Sound choices identify their supplying theme,
plugin or built-in origin alongside playback and attribution details.

The built-in default theme and the GitHub Pages site share one set of design tokens. `design/tokens.json` is the
single source for the colours, corners, type scale and widths: `scripts/design_tokens.py` generates the site's
`docs/assets/website/tokens.css` from it and `--check` (part of `make lint` and the unit tests) fails when the web app's
CSS, `DEFAULT_THEME`, the avatar palette or the site's stylesheets drift from it.

## Themes that follow the time of day and the weather

A theme can declare variants ([ADR 0060](adr/0060-themes-follow-the-time-of-day-and-the-weather.md)): colours that
apply when the **signals** match. There are two, with a fixed vocabulary:

| Signal | Values | From |
| --- | --- | --- |
| `daypart` | `dawn`, `morning`, `day`, `dusk`, `evening`, `night` | the core: your `[org] timezone`, and sunrise and sunset when a plugin knows them |
| `weather` | `clear`, `clouds`, `rain`, `drizzle`, `thunder`, `snow`, `fog`, `wind`, `heat`, `cold` | a plugin's `@module.signals` operation |

`hz system theme signals` shows them now. A variant's `when` names one or both, each a word or a list of words; the
matching variant naming the most signals wins, the first in the file on a tie. With no plugin publishing `weather`, a
variant that needs it never applies, and the base colours do. A variant sets any colour token, `main_tint` and
`backdrop` for `light` and/or `dark`; everything else stays the base's. When the variant changes, the colours fade
over 1.5 s (not with reduced motion).

A **backdrop** is a gradient behind the main area only (the sidebar and cards stay solid): `gradient` is `linear` or
`radial`, `angle` 0 to 360 (linear), `stops` two to four hex colours or colour tokens of the theme (`primary`,
`main_tint`, `background`, ...), `opacity` up to 0.9 (default 0.6). Prefer tokens as stops: they follow the mode and
fade with the variant.

**Contrast is checked at import**, in both modes, for the base and every variant: `primary_foreground` on `primary`, and
`foreground` and `muted_foreground` on `card`,
`sidebar`, `popover`, `background`, `main_tint` and every backdrop stop over the background, at
least 4.5:1. A theme that fails is refused with each failing pair named, e.g. `variant.2.light: muted_foreground on
backdrop stop #cfd8dc is 4.47:1, under 4.5:1`. Primary buttons keep opaque fills on hover so the checked pair
remains valid; an inset ring provides the hover indication.

## How to make a weather theme

A plugin publishes the weather with a read-only operation that returns `ThemeSignals`; the core reads it at most
once per `every` and leaves it unknown when it fails. Keep it quick: cache the forecast in the service.

```python
from pathlib import Path

from heinzel import Module, ThemeSignals

module = Module("weather", "The weather where you are, for Today and for themes.")
module.theme(Path(__file__).parent / "themes")


@module.signals(every="15m")
def now(service: WeatherService) -> ThemeSignals:
    """The weather now, for themes."""
    current = service.current()  # cached; no request when fresh
    return ThemeSignals(weather=current.kind, sunrise=current.sunrise, sunset=current.sunset)
```

The theme, `themes/weather.toml`: a calm base, a sky-blue clear day, a warm dusk gradient, a deep indigo night, grey
rain and a pale snow. It passes the contrast check as it is; `tests/unit/kernel/themes_test.py` imports it.

```toml
name = "weather"
title = "Weather"
font_sans = 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif'
font_mono = 'ui-monospace, "SF Mono", Menlo, monospace'
border_width = "1px"

[light]
background = "#f8fafc"
foreground = "#0f172a"
muted = "#eef2f6"
muted_foreground = "#475569"
card = "#ffffff"
border = "#dde3ea"
primary = "#0369a1"
primary_foreground = "#ffffff"
accent = "#e0eef8"
ok = "#15803d"
warn = "#a16207"
bad = "#b91c1c"
ring = "#0369a1"
sidebar = "#f1f5f9"
input = "#ffffff"
popover = "#ffffff"

[dark]
background = "#0b1220"
foreground = "#e2e8f0"
muted = "#162033"
muted_foreground = "#94a3b8"
card = "#111a2b"
border = "#1f2a3d"
primary = "#38bdf8"
primary_foreground = "#0b1220"
accent = "#1a2a40"
ok = "#4ade80"
warn = "#facc15"
bad = "#f87171"
ring = "#38bdf8"
sidebar = "#0e1626"
input = "#162033"
popover = "#111a2b"

# A clear morning or day: sky blue fading into the page.
[[variant]]
when = { daypart = ["morning", "day"], weather = "clear" }
light = { main_tint = "#e0f2fe", backdrop = { gradient = "linear", angle = 180, stops = ["#bae6fd", "background"], opacity = 0.6 } }
dark = { main_tint = "#0c2a3f", backdrop = { gradient = "linear", angle = 180, stops = ["#0c4a6e", "background"], opacity = 0.4 } }

# Dusk: a warm sunset band, whatever the weather.
[[variant]]
when = { daypart = "dusk" }
light = { main_tint = "#fde7d3", backdrop = { gradient = "linear", angle = 180, stops = ["#fdba74", "#fbcfe8", "background"], opacity = 0.45 } }
dark = { main_tint = "#3b2130", backdrop = { gradient = "linear", angle = 180, stops = ["#7c2d12", "#4a1d3f", "background"], opacity = 0.35 } }

# Night: deep indigo, a glow at the top like a sky with a moon behind the clouds.
[[variant]]
when = { daypart = ["evening", "night"] }
light = { main_tint = "#e7e8f7" }
dark = { background = "#0f1030", card = "#16173d", sidebar = "#0c0d28", main_tint = "#1b1d4a", backdrop = { gradient = "radial", stops = ["#2a2d6e", "background"], opacity = 0.6 } }

# Rain, drizzle and thunder: grey, any time of day.
[[variant]]
when = { weather = ["rain", "drizzle", "thunder"] }
light = { background = "#f3f4f6", main_tint = "#e5e7eb", backdrop = { gradient = "linear", angle = 180, stops = ["#d1d5db", "background"], opacity = 0.5 } }
dark = { background = "#15181c", main_tint = "#22272e", backdrop = { gradient = "linear", angle = 180, stops = ["#2f3640", "background"], opacity = 0.5 } }

# Rain at night: darker still. It names two signals, so it beats both the night and the rain variant.
[[variant]]
when = { daypart = ["evening", "night"], weather = ["rain", "drizzle", "thunder"] }
light = { main_tint = "#e2e4ec" }
dark = { background = "#0d1016", main_tint = "#1a1f29", backdrop = { gradient = "radial", stops = ["#262c38", "background"], opacity = 0.5 } }

# Snow: pale and cold.
[[variant]]
when = { weather = "snow" }
light = { main_tint = "#f1f5f9", backdrop = { gradient = "linear", angle = 180, stops = ["#ffffff", "#e2e8f0", "background"], opacity = 0.7 } }
dark = { main_tint = "#1e293b", backdrop = { gradient = "linear", angle = 180, stops = ["#334155", "background"], opacity = 0.4 } }
```

Animated wallpapers (drifting clouds, falling rain and snow, stars at night) come with layered wallpapers, the next
step of ADR 0060's More Information.

## Dressing the core Heinzelmännchen

A theme can decorate the original drawing without replacing personal choices. Add this optional table to its TOML:

```toml
[heinzel]
decorations = ["snow"]

[heinzel.palette]
hat = "#dceaf5"
shirt = "#386387"
scarf = "#b7daed"
```

The palette changes the default hat, shirt and scarf. A person who chose a hat or shirt colour keeps that choice.
Snow adds a cap patch (a baby keeps its bare head), static snow dots and a scarf, across ages, builds and states.
A scarf already requested is drawn once. Selecting a theme without the table restores the original look immediately.
These three hex-colour keys and the `snow` decoration are checked. The theme list on the CLI, MCP and web includes
the table, so an agent can inspect it too ([ADR 0064](adr/0064-themes-can-dress-the-core-heinzelmaennchen.md)).

### Original theme artwork

A theme may also ship its own SVG hat and outfit layers. Put their relative paths in the optional table:

```toml
[heinzel.assets]
hat-young = "avatars/snow-small-cap.svg"
hat-grown = "avatars/snow-medium-cap.svg"
hat-old = "avatars/snow-tall-cap.svg"
outfit-baby = "avatars/small-scarf.svg"
outfit-young = "avatars/scarf.svg"
outfit-grown = "avatars/scarf.svg"
outfit-old = "avatars/scarf.svg"
```

Every slot is optional. Hats use `viewBox="0 -240 512 840"` in the head's own coordinates; outfits use
`viewBox="0 0 512 860"` in the full figure's coordinates. The host scales hats with the head and outfits with shoulder
width. Hat layers move with cap gestures. Outfit layers sit under explicit accessories. Keep the face and personal
colours visible; these layers add clothing details rather than replacing the character. A baby has no hat slot.
Use the five look choices and every state to review an asset before shipping it.

Files must stay inside the theme directory, end in `.svg`, fit within 200 KB and pass the same SVG checks as logos.
Scripts, event handlers, entities and external references are refused. Avatar layers must be static: use SVG
presentation attributes, without SMIL animation, stylesheets or inline CSS, so reduced-motion preferences remain
under the host's control. The host loads them as images and checks
files again when resolving URLs or serving bytes. A missing or changed unsafe layer falls back to the original
avatar. The theme listing's `heinzel_assets` contains only available, content-versioned same-origin URLs; the
`heinzel.assets` file paths are omitted from listings. Selecting another theme updates mounted avatars immediately.

Do not combine a scarf asset with the built-in `snow` decoration unless both are intentionally designed to layer.
App/PWA, native notification, face and splash replacements remain future extensions; the existing white app icons
stay in place. See [ADR 0073](adr/0073-themes-supply-safe-avatar-layers.md).
