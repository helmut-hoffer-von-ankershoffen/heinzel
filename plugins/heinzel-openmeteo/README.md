# heinzel-openmeteo

The weather where you are, from [Open-Meteo](https://open-meteo.com), with no account and no API key: the `weather`
capability ([ADR 0028](../../docs/adr/0028-integrations-are-capability-adapters.md)), served by a first-party
[Heinzelmännchen](../../README.md) plugin. It is released with the core, at the same version, and is not installed by
default:

```bash
hz system plugin add openmeteo --confirm       # the release matching your hz; run it again to upgrade
hz system plugin remove heinzel-openmeteo
hz setup openmeteo                             # your places (without one, `hz weather` asks for `--location`)
hz weather                                     # now, and the next three days at your primary place; --json for agents
```

The command is the core's `hz weather` ([the weather module](../../docs/MODULES.md#weather)), which asks for the
`weather` capability and never for this plugin: a company's own plugin can serve the capability instead, and nothing
else changes. While this is the only weather adapter installed it is bound automatically
([ADR 0055](../../docs/adr/0055-connectors-show-their-sign-in-test-live-on-demand-and-bind-themselves-when-alone.md)).

## Where the place comes from

- **Settings**: `[weather] locations`, yours first; `hz weather` uses the first unless you pass `--location`.
- **A name**: geocoded by Open-Meteo's free geocoding API. What follows a comma narrows it by country code, country
  or region (`Lisbon, PT`, `Portland, Oregon`); the best match without one.
- **Coordinates**: `38.72,-9.14`, used as they are; nothing is geocoded.

## What it returns

`WeatherReport` (`heinzel.WeatherReport`): the place as resolved, the weather now (summary, temperature, what it feels
like, humidity, wind, rain in the last hour) and five days (summary, high, low, chance of rain, rain), all metric;
`hz weather` converts to your units (`weather.units`, imperial in the US, Liberia and Myanmar). Summaries come from
Open-Meteo's WMO weather codes.

```bash
hz openmeteo info                       # this plugin, its version and the hosts it reaches
hz system connector test openmeteo      # one live read at fixed coordinates, only when you ask
hz system capability list               # who reads what, through which adapter and hosts
```

## Attribution

Weather data by [Open-Meteo.com](https://open-meteo.com), licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Its free service is for non-commercial use; a company that
needs more should serve the `weather` capability from its own plugin. A place needs a country, region or country code
spelled as Open-Meteo knows it (`Portland, Oregon`, not `Portland, OR`); without one the best match is used.

This replaces the community `heinzel-weather` plugin (OpenWeather, needs a key) for the plain case; do not install both
adapters of one capability without choosing between them.

## Privacy

The places you ask about go to `api.open-meteo.com` and `geocoding-api.open-meteo.com`, which
`hz system capability list` shows. Nothing else is sent, no key or account is involved, and the requests are plain
`GET`s over HTTPS that refuse redirects. Open-Meteo's free service is for non-commercial use and rate limited; see
[its terms](https://open-meteo.com/en/terms). Errors never print the request, which holds your place.
