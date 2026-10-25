# Themes

A theme is data a plugin ships ([ADR 0044](adr/0044-themes-are-data-a-plugin-ships.md)): a TOML file with the
colours of the web app's tokens for light and dark, fonts, sizes and an optional logo, offered with
`module.theme(Path(__file__).parent / "themes")`. `hz system theme list` shows the installed ones, `hz system theme
use <name>` picks one. Every value passes a grammar at import, so a theme can't load or run anything.

Soundscapes work the same way ([ADR 0058](adr/0058-soundscapes-are-data-a-plugin-ships-chosen-like-a-theme.md)): a plugin
offers its own with `module.soundscape(Path(__file__).parent / "soundscapes")`, and they appear in `hz system sound
list` and the web app's picker beside Claude FM.

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

**Contrast is checked at import**, in both modes, for the base and every variant: `foreground` and `muted_foreground` on `card`,
`sidebar`, `popover`, `background`, `main_tint` and every backdrop stop over the background, at
least 4.5:1. A theme that fails is refused with each failing pair named, e.g. `variant.2.light: muted_foreground on
backdrop stop #cfd8dc is 4.47:1, under 4.5:1`.

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
