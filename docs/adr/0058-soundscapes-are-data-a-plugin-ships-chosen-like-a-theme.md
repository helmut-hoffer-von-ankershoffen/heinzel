---
status: proposed
date: 2026-10-24
decision-makers: maintainer
amends: 0027, 0039, 0046
amended-by: 0088, 0092, 0095
related: 0003, 0018, 0026, 0040, 0043, 0044
---

# 0058: Soundscapes are data a plugin ships, chosen like a theme: `module.soundscape(...)`, `hz system sound use`

## Context and Problem Statement

The maintainer asked for soundscapes in the web app: quiet background sound a person picks the way they pick a theme
([ADR 0044](0044-themes-are-data-a-plugin-ships.md)), but on its own, so either can change without the other. Plugins
contribute them, and the core initially shipped two: CEO mode, a stream from the CEO mode YouTube Live channel, and
Claude FM, a stream on the official Claude YouTube channel
(`https://www.youtube.com/watch?v=tRsQsTMvPNg`). There are two kinds. **Layered** soundscapes are licensed audio files
in the plugin, mixed live, each layer louder or softer with a signal such as rain intensity or the part of the day.
**Streams** play in the official player of an allowed host. Sound must stay quiet when it should: it is off until the
person presses Play (browsers block autoplay anyway), it ducks while they type in the chat, and it mutes during a
meeting. The web head's CSP allows only its own origin, and no frame at all
([ADR 0039](0039-a-web-head-a-react-app-served-by-the-api-head.md),
[ADR 0046](0046-an-installable-web-app-with-paired-devices.md)). The core contacts no service the person didn't set up
([ADR 0027](0027-nothing-is-sent-home.md)). So the questions are: what a soundscape is, how a plugin declares one, how
its files are checked and served, which host a stream may come from, and how the CSP admits that host without being
widened for people who never play anything.

## Decision Drivers

- Data, not code (ADR 0044): a plugin offers a soundscape without UI code, and it can be checked without a browser.
- Nothing loads before Play: no frame, no request and no cookie from a third party until the person presses Play.
- A plugin or a project file can't name a host: the frame origin comes from a table in code.
- One choice for every head ([ADR 0003](0003-heads-are-views-on-operations.md)): the CLI, MCP and the web app see and
  set the same setting.
- Quiet by default: off, ducks while typing, mutes in meetings, and still works without a calendar.

## Considered Options

- **A `SoundscapeLet` per soundscape, declared with `module.soundscape(Soundscape | Path)` from TOML; consumed by
  `system.sound`; `[system.sound]` is a user setting; `frame-src` is widened for the one host of the stream in effect,
  on the HTML page only** (chosen)
- **Reuse `ThemeLet`, a theme with sound**: the choice is separate by the maintainer's decision, and the asset kinds and
  checks differ. A theme's token contract must not grow audio fields.
- **A free stream URL in the soundscape**: any plugin could embed any site under our origin's CSP. Refused for that
  reason. Providers are an allowlist in code.
- **`frame-src https://www.youtube-nocookie.com` always**: widens the CSP for everyone, including people who never play
  anything.
- **Claude FM as a kernel constant**: it is content, not mechanism (INTENT tenets 8 and 9). It ships as a TOML file that
  `system.sound` offers through the same let, and a company layer can shadow it.
- **The volume and the play state on the server**: whether sound plays depends on a gesture in one browser tab, and
  only the browser knows it. The volume is a setting (`hz system config set system.sound.volume`), saved debounced.

## Decision Outcome

Chosen option: **a `SoundscapeLet` per soundscape, consumed by `system.sound`, with the frame origin from code,
widened on the page only while a stream is in effect**. This keeps soundscapes typed data the kernel collects like
themes, and the only third party is one allowlisted host that the person starts with an explicit Play.

- **A soundscape** (`Soundscape`, `heinzel`, strict like `Theme`):
  - `name`: a slug. `off` is reserved.
  - `title`: one printable line, at most 64.
  - `kind`: `layers` or `stream`.
  - `pairs_with`: theme names, at most 8.
  - `fade_seconds`: 1 to 30, 4 by default.
  - For `layers`: `layers` (1 to 8 `SoundLayer`) and `earcons` (a table keyed by notice kind,
    [ADR 0043](0043-desktop-notifications-one-policy-shown-once.md)).
  - For `stream`: `stream` (`SoundStream`).
  - The fields of the other kind are refused.
- **A layer and an earcon** (`SoundLayer`, `SoundEarcon`) share these fields:
  - `file`: a path relative to the soundscape file's directory. It follows the theme font files' path rules and ends in
    `.ogg`, `.opus` or `.mp3`.
  - `licence`: one of `CC0-1.0`, `PDDL-1.0`, `CC-PDDC`, `CC-BY-3.0` or `CC-BY-4.0`.
  - `attribution`: one printable line of at most 200 characters, required for `CC-BY-*`.
  - `source`: an https URL without credentials, optional.

  A layer adds:
  - `gain`: 0 to 1.
  - `signal`: `none`, `daypart`, `weather.rain` or `weather.wind`.
  - `curve`: for a numeric signal, 2 to 8 points `[x, y]` in 0..1 with x rising, read piecewise linear; for `daypart`,
    a table of `dawn`, `morning`, `day`, `dusk`, `evening` and `night` mapped to 0..1. A missing part is 0.

  A layer whose signal is absent plays at its `gain`.
- **Asset checks.** These run at import and again on every read, as for fonts (ADR 0044):
  - The file must exist and, with symlinks resolved, lie inside the directory.
  - It is read through one descriptor that follows no symlink and must be a regular file.
  - Size limits: at most 2 MB per file and 8 MB per soundscape.
  - The extension must match the signature. `.ogg` and `.opus` start with `OggS` and are served as `audio/ogg`. `.mp3`
    starts with an `ID3` tag, or with two consecutive valid MPEG audio frame headers, and is served as `audio/mpeg`.
- **A stream** (`SoundStream`) has a `provider` and a `video`:
  - `provider` comes from `STREAM_PROVIDERS`, a table in `heinzel.kernel`. Today it holds only `youtube`, which maps
    to the privacy-enhanced embed `https://www.youtube-nocookie.com/embed/<video>` and to that frame origin.
  - `video` is an id of 11 letters, digits, `-` or `_`.
  - No URL or host is data.
- **Signals** (`heinzel.kernel._signals`): the names a layer can follow, `SIGNALS` (`daypart`, `weather.rain`,
  `weather.wind`). The part of the day is the one of themes (ADR 0060, `daypart` in `_theme_signals.py`): `dawn`,
  `morning`, `day`, `dusk`, `evening` and `night`, from the sun times where a module knows them.
  - `hz system sound list` returns the current values, `weather.*` absent until a plugin publishes them.
  - `weather.*` stay absent until a plugin publishes them; ADR 0060's categorical `weather` doesn't fill them yet.
- **Operations** (`system.sound`, [ADR 0018](0018-one-administrative-namespace.md)):
  - `hz system sound list` is read-only and on every head. It returns
    `{selected, effective, soundscapes, signals, volume, follow_theme, mute_in_meetings, duck_while_typing, note, next}`.
    Each soundscape carries `name, title, kind, module, plugin, selected, effective, pairs_with, fade_seconds, stream,
    layers, earcons, shadows`. Streams carry `provider`, `embed_url`, `frame_origin` and `watch_url`; layers and earcons
    carry served URLs with `?v=` (a hash of the content), `licence`, `attribution` and `source`.
  - `hz system sound use <name>` refuses a name no installed plugin offers and writes `[system.sound] name`. `off`
    always exists.
  - Ties over a name follow the layer order of themes (ADR 0040, 0044), conflicts included.
- **Settings** (`[system.sound]`, user scope; every key is `ignored_in_project`, so a cloned repository can't switch on
  a third-party stream or widen the CSP):
  - `name`: `off` by default.
  - `volume`: 0 to 100, 50 by default.
  - `follow_theme`: `false` by default.
  - `mute_in_meetings`: `true` by default.
  - `duck_while_typing`: `true` by default.

  An unknown or malformed name falls back to `off`, and the list says why.
- **Follow my theme.** When `follow_theme` is on and a soundscape `pairs_with` the theme in effect, that soundscape is in
  effect instead of the selected one. Of several, the most specific layer wins, then the name.
  - The theme in effect comes from `ThemeChoice` (a kernel interface, implemented by `system.theme`), so `system.sound`
    doesn't import `system.theme`.
  - Neither setting needs the other.
- **Serving files** (web head):
  - Layers are served at `/sound-assets/<name>/layer-<n>.<ext>` and earcons at
    `/sound-assets/<name>/earcon-<kind>.<ext>` (`SOUND_ROUTE`), outside `/api` and without the token, because the
    files are immutable per version and hold nothing private.
  - Every other check of the web head applies: a loopback Host, no other Origin or site.
  - The request is matched against patterns and looked up through `SoundAssets` (a kernel interface implemented by
    `system.sound`). It never builds a path, and it is refused unless the extension equals the declared file's.
  - Answers carry the type from the signature, `nosniff`, the immutable cache, no range support, and
    `SOUND_CSP = default-src 'none'; sandbox`.
  - The app fetches them with `fetch` and decodes them with Web Audio, so `connect-src 'self'` covers it. There is no
    `media-src` and no `<audio>`. A decoded layer longer than 120 s, or an earcon longer than 5 s, is refused in the
    browser.
- **The CSP of the page.** Only the HTML page (the app's `index.html`) gets `frame-src <origin>`, and only when the
  soundscape in effect is a stream. The origin is looked up from `STREAM_PROVIDERS` by the provider that
  `SoundAssets.stream_provider()` returns.
  - It is read per request, synchronously, in the page route. Any error means no widening (fail closed).
  - Every other response keeps the strict CSP: no frames.
  - After a change that adds or removes the frame origin, the app reloads the page once from the network. The service
    worker already fetches navigations network-first.
  - The service worker's offline shell may keep the last page's header. That is harmless, because no frame exists
    before Play.
- **The player.** Before Play, nothing of the host exists in the page: no iframe, no thumbnail (`img-src 'self'`).
  - Play creates the iframe:
    - `src`: `embed_url` with `autoplay=1`, `enablejsapi=1` and `origin=<our origin>`, built with `URL`, the id
      checked again.
    - `referrerpolicy="strict-origin-when-cross-origin"`: the page's `no-referrer` would break the embed, and this
      sends only `http://127.0.0.1:<port>/`.
    - `sandbox="allow-scripts allow-same-origin allow-presentation allow-popups allow-popups-to-escape-sandbox"`.
    - `allow="autoplay; encrypted-media"`.
  - It shows in a small visible card: the host's own player, never proxied.
  - The mixer only fades, ducks and mutes it, by `postMessage` commands (`setVolume`, `pauseVideo`, `playVideo`) sent to
    that origin, never `*`. It loads no script of the host into our page. Messages are accepted only from that origin
    and that frame's window, parsed in `try`, and never put into the page as HTML.
  - Off removes the iframe.
- **Mixing and quiet.** A pure state machine in the app (`web/src/sound/machine.ts`) decides the target gain:
  - The inputs are play and pause, the volume, typing in the chat (it ducks to 25% and releases 1.5 s after the last
    key), voice recording (`duck("voice")`, for the voice input to come), and a meeting in progress when
    `mute_in_meetings` is on.
  - The meeting comes from `hz org agenda`, which the footer already reads. It is unknown, so not muted, without a
    calendar.
  - Changes ramp over `fade_seconds`; a duck ramps over 0.3 s.
  - Earcons play for notices that arrive while sound plays, at the same gain.
  - Reduced motion doesn't concern sound, and a hidden tab keeps playing.
- **Controls.**
  - **The sidebar's account row** has a speaker button beside the light/dark toggle, in the collapsed rail too. Its
    click plays or pauses, and `aria-pressed` reflects playing.
  - **A chevron beside it** opens a popover with:
    - the soundscapes (Off, Claude FM, the plugins'), the current one checked, each stream marked "Plays from YouTube"
      and each CC-BY layer with its attribution;
    - the volume;
    - Follow my theme;
    - Mute during meetings.
  - **A pick plays and the popover stays** (amended): choosing a soundscape starts it at once, in the click that chose
    it, and switches over from the one that plays, so Play is a second click only for the pick that is already
    chosen. Pick and Pause/Play share the popover's header; Escape and a click outside close it. A stream whose host
    the page does not admit yet reloads the page once (see "The CSP of the page"); the reloaded page opens the
    popover again and plays the pick, within 30 seconds, with the browser's autoplay rules still in force.
  - **Settings → General** has the full Sound picker, from the same data.
  - **A "now playing" footer item** is deferred: a status item of the status-item lets
    proposed as ADR 0056, off by default, once that lands.
- **Amendments.**
  - **ADR 0027.** The core's own contact with a third party gains one case: the stream soundscape in effect, from the
    person's Play onwards. Claude FM is offered, never on by default.
  - **ADR 0039, 0046.** The page's CSP stays this origin's only, except for one `frame-src` origin from
    `STREAM_PROVIDERS`, on the HTML page, while a stream soundscape is in effect. `/sound-assets` is a route outside
    `/api`, like the theme routes.
- **Public API**, additive ([ADR 0026](0026-plugin-api-versioned-with-semver.md)):
  - in `heinzel`: `Soundscape`, `SoundLayer`, `SoundEarcon`, `SoundStream`, `SoundscapeLet`, `SoundAssets`,
    `ThemeChoice` and `Module.soundscape`;
  - in `heinzel.kernel`: `SOUND_ROUTE`, `SOUND_CSP`, `STREAM_PROVIDERS`, `SIGNALS`.

### Consequences

- Good: a plugin's soundscape is a TOML file and a few audio files. A weather plugin (heinzel-contrib) can layer rain
  and wind without code.
- Good: the only third party is a fixed host in code, and only from the person's Play onwards.
- Bad: once Play is pressed, YouTube gets the person's IP address and the local port (`origin=`), and can set storage
  in its frame even on the nocookie host. The popover says "Plays from YouTube", and SECURITY.md says so.
- Bad: a change between off and a stream reloads the page once.
- Neutral: licences are declared by the plugin, not proven. The check is that one of the allowed licences is named,
  with attribution where it requires one.

### Confirmation

- `tests/unit/kernel/soundscapes_test.py` checks the model:
  - each field's grammar, the kinds' exclusive fields, the licences and attribution, and curves;
  - asset paths that leave the directory (by `..`, absolutely or by a symlink), size limits per file and per
    soundscape, signatures (OGG, ID3, MP3 frames, a fake), an extension that doesn't match, and a file swapped after
    import;
  - `off` refused.
- `tests/integration/sound_test.py` checks the module and the web head:
  - the list through the CLI and the web head, `use` and its refusal, the fallback to `off`, project files ignored,
    and follow-my-theme with its tie rule;
  - the asset route's headers and refusals;
  - the page's CSP with `frame-src` exactly the YouTube nocookie origin only while a stream is in effect, and not on
    `/api` or asset answers.
  - It uses a tiny CC0 demo soundscape in `tests/fixtures/sound`.
- `web/src/sound/*.test.ts(x)` (vitest) checks the app:
  - the state machine (ducking and its release, meeting mute, volume, fades);
  - no iframe and no request to the host before Play, and the iframe's attributes after it;
  - Play only on a gesture, and postMessage's target and the origin check.
- `tests/unit/public_api_test.py` checks the new names.

## More Information

- **Amendment, 2026-10-26.** The built-in CEO mode stream was removed because its YouTube video cannot play embedded.
  Core no longer lists or installs it; the historical release note below records when it was first added. Claude FM
  remains the built-in stream.

- Deferred: the footer "now playing" status item (ADR 0056); real weather layers (a plugin's job, with
  plugin-published `weather`); a voice input that calls `duck("voice")`; playlists and other providers, each a new
  row of `STREAM_PROVIDERS` with its own review.
