---
status: proposed
date: 2026-10-24
decision-makers: maintainer
amends: 0039, 0043
related: 0019, 0020, 0027, 0030
---

# 0046: An installable web app on a fixed port, with paired devices and notifications from its service worker

## Context and Problem Statement

The web head ([ADR 0039](0039-a-web-head-a-react-app-served-by-the-api-head.md)) listens on a random port with a token
per process, kept in `sessionStorage`. That makes every `hz ui` a new origin and every new tab a new link, so the app
can't be installed: a progressive web app is bound to its origin, and an icon in the Dock that reopens to "open the link
`hz ui` printed" is no app. Notifications ([ADR 0043](0043-desktop-notifications-one-policy-shown-once.md)) need the
web app to show them while it is open, and the native notifier needs a stable address for a click to open. The
maintainer asked for an installable app with notifications (2026-10-24) and decided the points below.

## Decision Drivers

- Localhost only, nothing sent home (ADR 0027): no web push, no third-party host, the CSP stays `'self'`.
- A token a web page can't forge or make the browser send (ADR 0039): a header, never a cookie or a URL query.
- Reopening the installed app from the Dock must just work while `hz ui` runs.
- Whatever is kept on disk or in the browser must be revocable, and a leak of the state directory must not leak a
  working credential.

## Considered Options

- **A fixed default port, a per-device token minted with the session token, kept by the app, stored hashed and
  revocable; a service worker for notifications and the app's shell** (chosen)
- **Keep the per-process token in `localStorage`**: dies with every `hz ui`, so the Dock icon still needs a new link.
- **A cookie**: sent by the browser on its own, so it brings CSRF back (ADR 0039).
- **A long-lived token in the configuration file**: one secret for every browser, not revocable per device, readable
  by anything that reads the configuration.
- **Web Push**: the browser vendor's push service relays every notification over the internet (ADR 0043).

## Decision Outcome

Chosen option: **a fixed port, paired devices, a service worker**, because the installed app keeps its origin and its
credential, each device can be revoked on its own, and nothing new crosses the machine's boundary. Decided by the
maintainer, 2026-10-24.

- **The port.** `hz ui` listens on `[hz] ui_port` (8737), on 127.0.0.1 only, user scope (a project's `hz.toml` can't
  set it); `--port 0` still picks a free one for a throwaway session. The Host, Origin and `Sec-Fetch-Site` checks are
  unchanged. A second `hz ui` on the same port fails with "is `hz ui` running already?".
- **Paired devices** (amends ADR 0039's token model). `hz ui` still makes a session token per process and opens the
  browser with it in the URL fragment. With that session token, and only with it, the app asks once for a device token
  (`POST /api/_web/device`, outside the OpenAPI schema, behind the API's middleware) and keeps it in `localStorage`.
  Every later `hz ui` accepts it as `Authorization: Bearer`; the session token comes first when a tab has one.
  - A device token is `hzd_` and 32 random bytes. Only its SHA-256 is stored, in `state_dir/web/devices.json` (0700
    directory, 0600 file, written atomically under a lock), with the name the app gave (browser, system, installed or
    not; cleaned and bounded), when it was paired and when last used (to the hour). At most 20.
  - A device token never mints another one (403); the session token can. Lookups compare digests with
    `hmac.compare_digest`.
  - `hz ui --devices` lists them, `hz ui --revoke ID` (or `all`) unpairs; the app's Settings can unpair itself
    (`DELETE /api/_web/device`); a refused device token is dropped by the app, so the next link pairs again.
  - A device token unused for 30 days stops working and is removed; pairing again takes a link from `hz ui`.
  - Trade-off, accepted: a device token lives until it is revoked or goes unused, in the browser profile's storage,
    where any script of this origin could read it, which the CSP limits to the app's own. Someone with the person's
    browser profile can use the app while `hz ui` runs, as they could with the person's terminal. And because the
    origin is now fixed, a process that binds `127.0.0.1:<ui_port>` while `hz ui` isn't running (another local user,
    a stray dev server) is that origin to the browser: the Dock icon or a notification's click would load its page,
    which could read the device token. The expiry, `hz ui --revoke` and the doctor-visible list bound the damage; a
    machine shared with people you don't trust shouldn't pair a browser. The per-process token had no such window but
    made installing impossible.
- **The app is installable.** `manifest.webmanifest` (Heinzelmännchen, `hz`, standalone, `#181818`, icons 192, 512 and
  maskable 512 drawn like Heinzel Notifier's, an apple-touch-icon), `favicon.svg` and `favicon.ico`. "Install app"
  uses `beforeinstallprompt` on Chrome and Edge; Safari gets the line "File > Add to Dock". The CSP adds
  `worker-src 'self'`; `sw.js` and the manifest are served with `Cache-Control: no-cache`.
- **The service worker** (`sw.js`, same origin, scope `/`, no inline script): it caches the app's shell (the page,
  hashed assets, the manifest and an icon) so the installed app opens even while `hz ui` isn't running, and never
  caches API data; old caches go when a new version activates. It shows notifications with
  `registration.showNotification`; a click focuses an open window of the app and routes it, or opens one, at the
  notice's route with `?event=<id>`. Paths are this app's only.
- **Notifications in the app** (amends ADR 0043). `GET /api/system/notify/stream` is server-sent events read with
  `fetch` and the Bearer header, never `EventSource` with a token in the URL. A tab with notifications turned on (the
  browser's permission is asked from that switch or the test button, never on page load) opens it with
  `?popups=true`, claims due notices, pops them up through the service worker and acknowledges them; otherwise it only
  refreshes the list. "Send a test notification" records a test notice and, with `hz system notify test --native`,
  pops it up through the native notifier at once (it may let macOS ask, a person pressed the button; so `test` is for the CLI and the
  web head, not MCP), and through the browser too. The native notifier's click opens `http://127.0.0.1:<ui_port><route>?event=<id>`.

### Consequences

- Good: an icon in the Dock that opens Heinzelmännchen in its own window, with notifications, while `hz ui` runs.
- Good: each browser is a revocable device; the state directory holds digests, not tokens.
- Bad: a credential that outlives the process, in the browser; accepted above, with revocation.
- Bad: one fixed port to keep free; `--port` and `[hz] ui_port` move it, which makes installed apps a new origin.
- Neutral: the app still needs `hz ui` running; starting it on login is the daemon's to offer later.

### Confirmation

- `tests/integration/web_devices_test.py`: pairing with the session token only; device tokens open the app and are
  stored as digests (0600/0700); unknown, malformed and revoked tokens are refused; the REST head's refusals apply to
  device tokens; `hz ui --devices` and `--revoke`; bounded, cleaned names; a damaged file accepts nothing.
- `tests/integration/notify_stream_test.py`: the stream's refusals and what it sends with and without `popups`.
- `tests/integration/web_security_test.py`: the CSP with `worker-src 'self'`, the manifest's type and the worker's
  cache header.
- `web/src/notifications/notifications.test.tsx`, `web/src/pwa/pwa.test.ts`: the permission asked only from a click,
  the test button, notifications through the service worker with the notice's path, pairing and dropping refused
  device tokens, the install states.

## More Information

- Decided by the maintainer, 2026-10-24: a fixed port, a persistent device token kept by the app and revocable, a
  manifest and service worker, "Install app", notifications through the service worker and the native click opening
  the app.
- Later: `hz ui` started at login by the daemon's unit, so the Dock icon always finds it.

**Amendment 2026-10-24: a splash screen while hz starts.** Requested by the maintainer: the app shows a splash from
the first paint until it is ready, in the person's last theme and mode, and the fix instead of a spinner when hz
doesn't answer.

- **What it shows.** The theme's branding (its logo through an `<img>`, or the app's own icon file, `/favicon.svg`, and
  its name, so new artwork reaches the splash unchanged), hz's
  version, one line for what is happening ("Starting…", "Connecting to hz…", "Signing you in…", "Restarting hz…",
  "Reconnecting to hz…") in a `role="status"` region, and a progress bar (one element, `#splash-progress`, that an animation file can replace) which holds still for reduced motion. It stays
  at least 300 ms and fades out in 200 ms once hz answered with the token accepted and the theme is applied. After 8 s
  without an answer it shows what to do: hz ui isn't running, or it no longer accepts this page's token, with the
  command. It comes back over the app on `POST /api/restart` and when hz stops answering, and leaves the person where
  they were. The state machine is `web/src/splash/machine.ts`.
- **Before React.** `web/index.html` holds the splash's markup, its critical CSS and one small inline script that reads
  the mode (`hz.theme`) and a snapshot of the last theme (`hz.splash`: four colours, the font stack and the logo's URLs,
  each checked against the grammar of ADR 0044 again, and the version) from `localStorage`, inside `try`, so a page
  with storage disabled paints the default theme. The web app writes the snapshot whenever the theme changes.
- **The CSP** allows that one script by its hash (`'sha256-…'` in `script-src`, `SPLASH_SCRIPT` in the web head), not by
  `'unsafe-inline'`: every other inline script stays refused, and a changed script fails a test until the hash changes
  with it. The splash's CSS is inline under the existing `style-src 'unsafe-inline'` (ADR 0045's reason, the terminal).
- **The installed app.** The manifest's `background_color` and `theme_color` are the default theme's dark background,
  the one colour a manifest can hold; the page's `theme-color` follows the mode and the last theme.
- **Confirmation**: `web/src/splash/*.test.ts(x)` (the machine's phases and timings, the inline script with storage
  disabled and with values that fail the grammar, reduced motion, the fix, axe), `tests/integration/web_security_test.py`
  (the hash matches the script, no other inline script is allowed), `tests/integration/web_pwa_test.py` (the colours
  agree with the default theme) and `tests/e2e/web_ui_test.py` (the splash with a slow API in light and dark, gone by
  itself, and the fix when hz is gone).
